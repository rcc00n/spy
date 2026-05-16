from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from apps.monitoring.services.facebook_auth import (
    FacebookAuthError,
    facebook_base_context_kwargs,
    get_facebook_login_credentials,
    record_facebook_credential_refresh_error,
    record_facebook_credential_refresh_success,
    refresh_facebook_storage_state_with_credentials,
    save_facebook_storage_state,
    submit_facebook_login,
    wait_for_facebook_authentication,
)


class Command(BaseCommand):
    help = "Create or refresh the saved Facebook Playwright login session."

    def add_arguments(self, parser):
        parser.add_argument(
            "--headless",
            action="store_true",
            help=(
                "Run Chromium headless. Uses --email/--password, env credentials, "
                "or active admin-stored credentials."
            ),
        )
        parser.add_argument(
            "--email",
            default="",
            help="Facebook login email. Defaults to FACEBOOK_LOGIN_EMAIL.",
        )
        parser.add_argument(
            "--password",
            default="",
            help="Facebook login password. Defaults to FACEBOOK_LOGIN_PASSWORD.",
        )
        parser.add_argument(
            "--timeout",
            type=int,
            default=180,
            help="Seconds to wait for login completion.",
        )

    def handle(self, *args, **options):
