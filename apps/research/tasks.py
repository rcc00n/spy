import time

from celery import shared_task
from django.conf import settings
from django.db import transaction

from apps.research.models import ResearchEvent, ResearchJob, ResearchSource
from apps.research.services.engine import ResearchEngineClient, ResearchEngineError
from apps.research.services.runpod import RunPodLifecycleClient


def record_event(
    job: ResearchJob,
    message: str,
    level: str = ResearchEvent.Level.INFO,
    payload=None,
    dedupe_last: bool = False,
) -> None:
    if dedupe_last:
        last_event = job.events.order_by("-created_at", "-id").first()
        if last_event and last_event.message == message and last_event.level == level:
            return

    ResearchEvent.objects.create(
        job=job,
        level=level,
        message=message,
        payload=payload or {},
    )


def sync_sources(job: ResearchJob, sources: list[dict]) -> None:
    if not sources:
        return

    source_rows = []
    for source in sources[:200]:
        if not isinstance(source, dict):
            continue
        source_type = str(
            source.get("source_type")
            or source.get("type")
            or ResearchSource.SourceType.OTHER
        )[:30]
        source_rows.append(
            ResearchSource(
                job=job,
                source_type=source_type,
                title=str(source.get("title") or "")[:500],
                url=str(source.get("url") or "")[:2000],
                excerpt=str(source.get("excerpt") or source.get("summary") or ""),
                payload=source,
            )
        )

    if not source_rows:
        return

    with transaction.atomic():
        ResearchSource.objects.filter(job=job).delete()
        ResearchSource.objects.bulk_create(source_rows)


def apply_remote_update(job: ResearchJob, update) -> ResearchJob:
    update_fields = []

    if update.external_job_id and job.external_job_id != update.external_job_id:
        job.external_job_id = update.external_job_id
        update_fields.append("external_job_id")

    if update.status and job.status != update.status:
        job.status = update.status
