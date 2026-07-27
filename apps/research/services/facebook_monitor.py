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
