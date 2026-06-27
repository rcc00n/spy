from celery import shared_task
from django.core.management import call_command
from django.utils import timezone

from apps.monitoring.models import ManualCheckJob
from apps.monitoring.services.runner import (
    accounts_for_check,
    format_check_summary,
    run_account_checks,
)
from apps.monitoring.services.telegram import send_system_alert


@shared_task
def check_active_accounts(force: bool = False) -> str:
    args = ["--force"] if force else []
    call_command("check_accounts", *args)
    return "completed"


@shared_task(bind=True)
def run_manual_check_job(self, job_id: int) -> str:
    job = ManualCheckJob.objects.select_related("account", "requested_by").get(pk=job_id)
    job.status = ManualCheckJob.Status.RUNNING
    job.started_at = timezone.now()
    job.task_id = self.request.id or job.task_id
    job.error_message = ""
    job.save(update_fields=["status", "started_at", "task_id", "error_message"])

    account_id = job.account_id
    accounts = accounts_for_check(force=True, account_id=account_id)
    if not accounts:
        job.status = ManualCheckJob.Status.ERROR
        job.finished_at = timezone.now()
        job.error_message = "No active monitored accounts found for manual check."
        job.summary = job.error_message
