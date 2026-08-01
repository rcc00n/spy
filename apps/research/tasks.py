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
        update_fields.append("status")
        record_event(job, f"Status changed to {job.get_status_display()}.")

    if update.plan and job.plan != update.plan:
        job.plan = update.plan
        update_fields.append("plan")

    if update.report_markdown and job.report_markdown != update.report_markdown:
        job.report_markdown = update.report_markdown
        update_fields.append("report_markdown")

    if update.error_message and job.error_message != update.error_message:
        job.error_message = update.error_message
        update_fields.append("error_message")

    if update.message:
        record_event(job, update.message, dedupe_last=True)

    if update_fields:
        update_fields.append("updated_at")
        job.save(update_fields=update_fields)

    sync_sources(job, update.sources)
    return job


def prepare_runpod_engine(job: ResearchJob) -> None:
    lifecycle = RunPodLifecycleClient()
    if not lifecycle.autostart_enabled:
        return

    record_event(job, "Starting RunPod pod for research engine.")
    lifecycle.start_pod()
    record_event(job, "Waiting for RunPod research engine health check.")
    lifecycle.wait_for_engine()
    record_event(job, "RunPod research engine is reachable.")


def maybe_stop_runpod_engine(job: ResearchJob) -> None:
    lifecycle = RunPodLifecycleClient()
    if not lifecycle.autostop_enabled:
        return

    active_jobs_exist = ResearchJob.objects.exclude(pk=job.pk).exclude(
        status__in=ResearchJob.TERMINAL_STATUSES
    ).exists()
    if active_jobs_exist:
        record_event(
            job,
            "RunPod pod left running because another research job is active.",
            level=ResearchEvent.Level.WARNING,
        )
        return

    try:
        lifecycle.stop_pod()
    except Exception as exc:
        record_event(
            job,
            f"Could not stop RunPod pod automatically: {exc}",
            level=ResearchEvent.Level.WARNING,
        )
    else:
        record_event(job, "RunPod pod stop requested after research job finished.")


@shared_task(bind=True)
def run_research_job(self, job_id: int) -> str:
    job = ResearchJob.objects.select_related("requested_by").get(pk=job_id)
    job.task_id = self.request.id or job.task_id
    job.save(update_fields=["task_id", "updated_at"])

    job.mark_started(ResearchJob.Status.PLANNING)
    record_event(job, "Research job started by portal worker.")

    try:
        prepare_runpod_engine(job)
        client = ResearchEngineClient()
        update = client.submit_job(job)
        job = apply_remote_update(job, update)
        external_job_id = job.external_job_id or update.external_job_id

        if job.status == ResearchJob.Status.COMPLETED:
            job.mark_finished(ResearchJob.Status.COMPLETED)
            record_event(job, "Research job completed by remote engine.")
            return "completed"

        if not external_job_id:
            raise ResearchEngineError(
                "Research engine did not return external_job_id, job_id, id, "
                "or a completed report."
            )

        poll_count = 0
        while poll_count < settings.RESEARCH_ENGINE_MAX_POLLS:
            time.sleep(settings.RESEARCH_ENGINE_POLL_SECONDS)
            poll_count += 1
            job.refresh_from_db()
            if job.is_terminal:
                return job.status

            update = client.get_job_status(external_job_id)
            job = apply_remote_update(job, update)

            if job.status == ResearchJob.Status.COMPLETED:
                job.mark_finished(ResearchJob.Status.COMPLETED)
                record_event(job, "Research job completed by remote engine.")
                return "completed"
