"""One durable queue step per task; beat recovers work after process/broker loss."""
import time
import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone
from playwright.sync_api import sync_playwright
from redis import Redis

from apps.monitoring.services.facebook_auth import facebook_context_kwargs
from apps.monitoring.services.sync_db import call_sync_db
from apps.research.models import ResearchEvent, ResearchJob
from apps.research.services.facebook_browser import (
    FacebookAccessStopped, canonical_post_url, collect_thread, discover,
)
from apps.research.services.facebook_store import (
    enqueue_url, finish_attempt, import_legacy_job, persist_snapshot, sync_source, update_report,
)

ACTIVE = ('queued', 'collecting', 'social_collecting')
logger = logging.getLogger(__name__)


def save_scan_result(job, result):
    """Compatibility importer for existing bounded scan reports."""
    for source in result['sources']:
        work = enqueue_url(job, source['url'], source.get('queries', []))
        if work:
            persist_snapshot(work.pk, source)
            work.refresh_from_db()
            sync_source(work)
    job.plan = {**job.plan, 'engine': 'facebook_browser', 'queries': result['queries']}
    update_report(job)
    # Historical callers can report links not yet canonicalized/imported.
    job.plan['unvisited_count'] = result['unvisited_count']
