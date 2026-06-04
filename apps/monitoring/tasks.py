from celery import shared_task
from django.core.management import call_command


@shared_task
def check_active_accounts(force: bool = False) -> str:
    args = ["--force"] if force else []
    call_command("check_accounts", *args)
    return "completed"
