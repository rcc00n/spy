from django.core.management.base import BaseCommand

from apps.monitoring.services.runner import (
    accounts_for_check,
    format_check_summary,
    run_account_checks,
)


class Command(BaseCommand):
    help = "Check active public social media accounts for keyword matches."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Check even if not due.")
        parser.add_argument("--account-id", type=int, help="Check one monitored account.")
        parser.add_argument(
            "--limit",
            type=int,
            help="Override max posts per account for this run only.",
        )
        parser.add_argument(
            "--no-telegram",
            action="store_true",
            help="Do not send Telegram alerts for new matches.",
