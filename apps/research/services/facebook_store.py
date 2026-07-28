"""Transactional checkpoints and an idempotent corpus independent of task lifetime."""
import hashlib
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.research.models import (
    FacebookComment, FacebookCommentRevision, FacebookPost, FacebookThreadWork,
    ResearchEvent, ResearchSource,
)
from apps.research.services.facebook_browser import canonical_post_url

LIMITS = {'comment_limit_reached', 'expansion_limit_reached', 'time_budget_reached', 'visible_comments_only', 'collecting', 'comments_not_observed', 'comment_links_not_matched'}


def enqueue_url(job, url, queries=()):
    url = canonical_post_url(url)
    if not url:
        return None
    post, _ = FacebookPost.objects.get_or_create(
        url_hash=hashlib.sha256(url.encode()).hexdigest(), defaults={'url': url})
    work, _ = FacebookThreadWork.objects.get_or_create(job=job, post=post)
    combined = list(dict.fromkeys(work.queries + list(queries)))
    if work.queries != combined:
        work.queries = combined
        work.save(update_fields=['queries', 'updated_at'])
    return work


@transaction.atomic
def persist_snapshot(work_id, snapshot):
    """Commit each visible batch. Never erase evidence on an error/empty response."""
    work = FacebookThreadWork.objects.select_for_update().select_related('post').get(pk=work_id)
    post = work.post
    if canonical_post_url(snapshot['url']) != post.url:
        raise ValueError('Checkpoint belongs to another post')
    now = timezone.now()
    if snapshot.get('public_verified'):
        post.public_verified = True
        post.last_seen_at = now
        if snapshot.get('text'):
            post.text = snapshot['text']
            post.mentions_pcl = snapshot.get('mentions_pcl', False)
        post.save()
        for record in snapshot.get('comments', []):
            if canonical_post_url(record.get('url', '')) != post.url or not record.get('id'):
                continue
            defaults = {'url': record['url'], 'parent_id': record.get('parent_id', ''),
                        'text': record.get('text', ''), 'has_media': record.get('has_media', False),
                        'mentions_pcl': record.get('mentions_pcl', False), 'last_seen_at': now}
            comment, created = FacebookComment.objects.get_or_create(
                post=post, facebook_id=record['id'], defaults=defaults)
            changed = created or comment.text != defaults['text'] or comment.has_media != defaults['has_media']
            if not created:
                for field, value in defaults.items():
                    setattr(comment, field, value)
                comment.save()
            if changed:
                FacebookCommentRevision.objects.create(comment=comment, text=comment.text, has_media=comment.has_media, observed_at=now)
            work.comments.add(comment)
    work.coverage = snapshot.get('coverage', 'collecting')
    work.checkpoint = {**work.checkpoint, 'sort': snapshot.get('sort', 'unknown'),
                       'saved_comments': work.comments.count(), 'last_checkpoint_at': now.isoformat(),
                       'public_verified_this_attempt': bool(snapshot.get('public_verified'))}
    work.save(update_fields=['coverage', 'checkpoint', 'updated_at'])
    return work.checkpoint['saved_comments']


def finish_attempt(work, snapshot=None, error='', blocked=False):
    work.refresh_from_db()
    coverage = (snapshot or {}).get('coverage', 'error')
    work.coverage = coverage
    work.error = error[:2000]
    new_comments = work.comments.count() - work.checkpoint.get('comments_before_attempt', 0)
    work.checkpoint = {**work.checkpoint, 'new_comments_last_attempt': new_comments}
    max_attempts = 1 if work.job.monitor_lane in ('refresh', 'backfill') else 3
    if blocked:
        work.status = 'blocked'
    elif coverage == 'post_only_comments_not_requested':
        work.status = 'post_only'
    elif coverage == 'no_new_comments_after_scroll':
        work.status = 'sampled'
    elif coverage in LIMITS:
        work.status = 'queued' if work.cycle_attempts < max_attempts else 'partial'
    else:
        # A missing public badge is a gap, never permission to collect private text.
        work.status = 'queued' if work.cycle_attempts < max_attempts else 'failed'
    work.next_attempt_at = timezone.now() + timedelta(seconds=30 if not error else 60 * work.cycle_attempts)
    work.save()
    sync_source(work)
    ResearchEvent.objects.create(job=work.job, level='warning' if error or work.status in {'partial', 'failed', 'blocked'} else 'info',
        message=f'Discussion attempt {work.attempts}: {work.post.url} — {work.coverage}; {work.comments.count()} saved, {new_comments} new.',
        payload={'work_id': work.pk, 'status': work.status, 'error': work.error})


def sync_source(work):
    """Keep the existing evidence UI compatible; corpus remains the source of truth."""
    work.post.refresh_from_db()
    post = work.post
    comments = [{'id': c.facebook_id, 'parent_id': c.parent_id, 'url': c.url, 'text': c.text,
                 'has_media': c.has_media, 'mentions_pcl': c.mentions_pcl,
                 'first_seen_at': c.first_seen_at.isoformat(), 'last_seen_at': c.last_seen_at.isoformat()}
                for c in work.comments.order_by('pk')]
    payload = {'url': post.url, 'text': post.text, 'public_verified': post.public_verified,
               'mentions_pcl': post.mentions_pcl, 'comments': comments, 'coverage': work.coverage,
               'status': work.status, 'sort': work.checkpoint.get('sort', 'unknown'), 'error': work.error,
               'attempts': work.attempts, 'queries': work.queries}
