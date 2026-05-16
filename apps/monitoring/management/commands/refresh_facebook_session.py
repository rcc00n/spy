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
        email = options["email"] or settings.FACEBOOK_LOGIN_EMAIL
        password = options["password"] or settings.FACEBOOK_LOGIN_PASSWORD
        headless = options["headless"]
        timeout_ms = max(1, options["timeout"]) * 1000
        credential = None

        if bool(email) != bool(password):
            raise CommandError("Provide both Facebook email and password, or neither.")

        if not email and not password:
            email, password, credential = get_facebook_login_credentials()

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=headless)
                try:
                    if headless:
                        state_path = refresh_facebook_storage_state_with_credentials(
                            browser,
                            username=email,
                            password=password,
                            timeout_ms=timeout_ms,
                        )
                    else:
                        state_path = self._refresh_with_headed_browser(
                            browser,
                            email,
                            password,
                            timeout_ms,
                        )
                finally:
                    browser.close()
        except PlaywrightTimeoutError as exc:
            record_facebook_credential_refresh_error(
                getattr(credential, "pk", None),
                str(exc),
