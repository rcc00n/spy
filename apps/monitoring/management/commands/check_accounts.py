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
        )

    def handle(self, *args, **options):
        post_limit = options.get("limit")
        if post_limit is not None and not 1 <= post_limit <= 20:
            self.stderr.write("--limit must be between 1 and 20.")
            return

        accounts = accounts_for_check(
            force=options["force"],
            account_id=options.get("account_id"),
        )

        if not accounts:
            self.stdout.write("No monitored accounts are due.")
            return

        for account in accounts:
            self.stdout.write(f"Checking {account}")
        summary = run_account_checks(
            accounts,
            send_telegram=not options["no_telegram"],
            post_limit=post_limit,
        )
        for run in summary["runs"]:
            self.stdout.write(
                f"Finished {run.monitored_account}: {run.status} "
                f"posts={run.posts_found} new={run.new_posts_found} "
                f"matches={run.matches_found} error={run.error_message[:160]}"
            )
        self.stdout.write(f"Summary: {format_check_summary(summary)}")
