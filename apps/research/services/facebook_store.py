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
