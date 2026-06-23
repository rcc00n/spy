import logging

from django.utils import timezone

from apps.monitoring.models import CheckRun, MonitoredAccount
from apps.monitoring.services.checker import random_delay, run_account_check
from apps.monitoring.services.telegram import send_system_alert


logger = logging.getLogger(__name__)


def accounts_for_check(*, force: bool = False, account_id: int | None = None):
    queryset = MonitoredAccount.objects.filter(is_active=True).order_by(
        "last_checked_at",
        "id",
    )
    if account_id:
        queryset = queryset.filter(pk=account_id)

    accounts = list(queryset)
    if not force:
        now = timezone.now()
        accounts = [account for account in accounts if account.is_due(now)]
    return accounts


def run_account_checks(
    accounts,
    *,
    send_telegram: bool = True,
    post_limit: int | None = None,
) -> dict:
    summary = {
        "accounts_checked": 0,
        "posts_found": 0,
        "new_posts_found": 0,
        "matches_found": 0,
        "errors": 0,
        "auth_required": 0,
        "runs": [],
    }

    for index, account in enumerate(accounts, start=1):
        if index > 1:
            random_delay()
        run = run_account_check(
            account,
            send_telegram=send_telegram,
            post_limit=post_limit,
        )
        summary["accounts_checked"] += 1
        summary["posts_found"] += run.posts_found
        summary["new_posts_found"] += run.new_posts_found
        summary["matches_found"] += run.matches_found
        if run.status == CheckRun.Status.ERROR:
            summary["errors"] += 1
        if run.status == CheckRun.Status.AUTH_REQUIRED:
            summary["auth_required"] += 1
        summary["runs"].append(run)
