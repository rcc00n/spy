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
