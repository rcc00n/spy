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
    job.save(update_fields=['plan'])
    return job.plan['verified_posts']


@shared_task
def dispatch_facebook_scans():
    # Persistent DB work remains discoverable even if task publication failed.
    # Round-robin by update time prevents a large scan monopolizing the browser.
    from apps.research.services.facebook_monitor import schedule_cycle, runnable_jobs
    schedule_cycle()
    jobs = runnable_jobs().order_by('updated_at')
    for job_id in jobs.values_list('pk', flat=True)[:10]:
        run_facebook_scan.delay(job_id)


def complete_if_idle(job):
    if job.facebook_discoveries.filter(status__in=['queued', 'running']).exists():
        return
    if job.facebook_threads.filter(status__in=['queued', 'running']).exists():
        return
    gaps = job.facebook_threads.filter(status__in=['partial', 'failed', 'blocked']).count()
    search_errors = sum(q.get('status') == 'error' for q in job.plan.get('queries', [])) + job.facebook_discoveries.filter(status__in=['failed', 'blocked']).count()
    message = f'Collection pass ended with {gaps} discussion gaps and {search_errors} search errors. Saved evidence retained; continue collection to retry.' if gaps or search_errors else ''
    job.mark_finished('failed' if gaps or search_errors else 'completed', message)
    ResearchEvent.objects.create(job=job, level='warning' if message else 'info',
        message=message or 'Collection pass ended. No further visible comments observed is not a completeness claim.')


def scan_step(job):
    import_legacy_job(job)
    if job.plan.get('discovery_version'):
        from apps.research.services.facebook_discovery import run_next_discovery
        if run_next_discovery(job):
            update_report(job)
            complete_if_idle(job)
            return
    queries = list(dict.fromkeys(line.strip() for line in job.query.splitlines() if line.strip()))[:12]
    index = len(queries) if job.plan.get('discovery_version') else job.plan.get('discovery_index', 0)
    depth = {'quick': (5, 2, 0), 'standard': (10, 4, 40), 'deep': (20, 7, 100)}.get(job.depth, (10, 4, 40))
    work = None
    if index >= len(queries):
        # An old RUNNING row means a previous task was interrupted; the Redis
        # lease guarantees no other collector is still reading it.
        work = job.facebook_threads.select_related('post').filter(status__in=['queued', 'running'], next_attempt_at__lte=timezone.now()).order_by('attempts', 'pk').first()
        if work is None:
            complete_if_idle(job)
            return
        work.status = 'running'
        work.attempts += 1
        work.cycle_attempts += 1
        work.error = ''
        work.checkpoint = {**work.checkpoint, 'comments_before_attempt': work.comments.count()}
        work.save()
        if work.cycle_attempts > 3:
            finish_attempt(work, error='Previous worker interrupted repeatedly; continue manually to retry.')
            update_report(job)
            complete_if_idle(job)
            return

    # One search or one discussion per browser lifetime. A task never depends
    # on an in-memory cursor surviving a worker restart.
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(**facebook_context_kwargs(auth_enabled=True))
            page = context.new_page()
            page.set_default_timeout(10000)
            if work is None:
                query = queries[index]
                entry = {'query': query, 'status': 'ok', 'discovered': 0}
                found = set()

                def save_urls(urls):
                    found.update(urls)
                    def save():
                        for url in urls:
                            enqueue_url(job, url, [query])
                    call_sync_db(save)

                try:
                    direct = canonical_post_url(query)
                    if direct:
                        save_urls([direct])
                    else:
