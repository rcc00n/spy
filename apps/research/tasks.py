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
