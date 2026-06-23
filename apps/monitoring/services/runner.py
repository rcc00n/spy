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
