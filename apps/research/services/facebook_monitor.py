"""Durable bounded recurring cycles; no network or broker dependency when scheduling."""
from datetime import timedelta

from django.db import transaction
from django.db.models import F, Max, OuterRef, Q, Subquery
from django.utils import timezone

from apps.research.models import (
    FacebookDiscoverySource, FacebookMonitor, FacebookPost, FacebookThreadWork,
    ResearchEvent, ResearchJob,
)
from apps.research.services.facebook_discovery import initialize_discovery
from apps.research.services.facebook_store import enqueue_url

ACTIVE = ('queued', 'collecting', 'social_collecting')
LANES = ('discovery', 'refresh', 'backfill')


def runnable_jobs():
    return ResearchJob.objects.filter(status__in=ACTIVE, plan__engine='facebook_browser').filter(
        Q(monitor__isnull=True) | Q(monitor__enabled=True))


def pause_monitors(reason):
    FacebookMonitor.objects.filter(enabled=True).update(
        enabled=False, pause_reason=reason[:2000], updated_at=timezone.now())


@transaction.atomic
def configure_monitor(action, values=None):
    monitor, _ = FacebookMonitor.objects.get_or_create(pk=1)
    monitor = FacebookMonitor.objects.select_for_update().get(pk=monitor.pk)
    if action == 'save':
        for field, value in (values or {}).items():
            setattr(monitor, field, value)
        # Changes take effect after the current cycle; no accumulated catch-up runs.
        for lane in LANES:
            setattr(monitor, lane + '_due_at', timezone.now() + timedelta(hours=getattr(monitor, lane + '_hours')))
    elif action == 'resume':
        monitor.enabled = True
        monitor.pause_reason = ''
    elif action == 'pause':
        monitor.enabled = False
        monitor.pause_reason = 'Paused by operator. The current browser step may finish saving evidence.'
    else:
        raise ValueError('Unknown monitor action')
    monitor.full_clean()
    monitor.save()
    return monitor


@transaction.atomic
def schedule_cycle(now=None):
    """DB lock serializes beat/admin calls; only one unfinished cycle per monitor."""
    now = now or timezone.now()
    monitor = FacebookMonitor.objects.select_for_update().filter(pk=1, enabled=True).first()
    if monitor is None or monitor.jobs.filter(status__in=ACTIVE).exists():
        return None
    lanes = sorted(LANES, key=lambda lane: getattr(monitor, lane + '_due_at'))
    for lane in lanes:
        if getattr(monitor, lane + '_due_at') > now:
            continue
        sources = []
        posts = []
        if lane == 'discovery':
            sources = list(FacebookDiscoverySource.objects.filter(enabled=True))
        elif lane == 'refresh':
            posts = list(FacebookPost.objects.filter(public_verified=True).order_by(
                F('refresh_queued_at').asc(nulls_first=True), 'pk')[:monitor.refresh_batch])
        else:
            # Caption-only and refresh passes do not overwrite the last deep-read outcome.
            latest_read = FacebookThreadWork.objects.filter(post_id=OuterRef('pk')).exclude(
                job__depth='quick').exclude(job__monitor_lane__in=['discovery', 'refresh']).order_by('-pk')
            posts = list(FacebookPost.objects.exclude(work_items__job__status__in=ACTIVE).annotate(last_read_status=Subquery(latest_read.values('status')[:1])).filter(
                Q(last_read_status__isnull=True) | Q(last_read_status__in=['post_only', 'partial', 'failed', 'blocked', 'queued', 'running'])
            ).order_by(F('backfill_queued_at').asc(nulls_first=True), 'pk')[:monitor.backfill_batch])
        setattr(monitor, lane + '_due_at', now + timedelta(hours=getattr(monitor, lane + '_hours')))
        monitor.save()
        if not sources and not posts:
            continue
        job = ResearchJob.objects.create(
            monitor=monitor, monitor_lane=lane, title=f'Facebook monitor: {lane}',
            query=f'Scheduled {lane} cycle', depth='deep' if lane == 'backfill' else 'standard',
            plan={'engine': 'facebook_browser', 'collector_version': 2, 'discovery_version': 1})
        if lane == 'discovery':
            initialize_discovery(job, sources, [])
        else:
            for post in posts:
                work = enqueue_url(job, post.url, [f'monitor: {lane}'])
                if lane == 'backfill':
                    # Reopening must replay already seen comments before progressing.
                    work.comments.set(post.comments.all())
                    work.attempts = post.work_items.aggregate(value=Max('attempts'))['value'] or 0
                    work.checkpoint = {'seeded_comments': work.comments.count()}
                    work.save()
                setattr(post, lane + '_queued_at', now)
                post.save(update_fields=[lane + '_queued_at'])
        ResearchEvent.objects.create(job=job, message=(
            f'Scheduled {lane}: {len(sources)} sources, {len(posts)} discussions. '
            'One bounded pass; intervals are queue targets, not coverage guarantees.'))
        return job
    return None
