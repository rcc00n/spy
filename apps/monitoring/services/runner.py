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

    logger.info(
        "Check summary accounts=%s posts=%s new=%s matches=%s errors=%s auth_required=%s",
        summary["accounts_checked"],
        summary["posts_found"],
        summary["new_posts_found"],
        summary["matches_found"],
        summary["errors"],
        summary["auth_required"],
    )
    if send_telegram and summary["accounts_checked"]:
        send_check_summary_alert(summary)
    return summary


def format_check_summary(summary: dict) -> str:
    return (
        f"accounts={summary['accounts_checked']} "
        f"posts={summary['posts_found']} "
        f"new_posts={summary['new_posts_found']} "
        f"matches={summary['matches_found']} "
        f"errors={summary['errors']} "
        f"auth_required={summary.get('auth_required', 0)}"
    )


def send_check_summary_alert(summary: dict) -> None:
    lines = [
        format_check_summary(summary),
    ]
    for run in summary["runs"]:
        account = run.monitored_account
        line = (
            f"{account.get_platform_display()} {account.account_name}: "
            f"{run.status} posts={run.posts_found} new={run.new_posts_found} "
            f"matches={run.matches_found}"
        )
        if run.error_message:
            line = f"{line} error={run.error_message[:300]}"
        lines.append(line)
    send_system_alert("Monitoring check finished", lines)
