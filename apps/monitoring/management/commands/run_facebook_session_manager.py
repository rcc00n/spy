import logging
import os
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from apps.monitoring.models import MonitoredAccount
from apps.monitoring.services.facebook_auth import (
    FacebookAuthError,
    facebook_base_context_kwargs,
    facebook_session_state,
    get_facebook_login_credentials,
    save_facebook_storage_state,
)
from apps.monitoring.services.facebook_checker import (
    detect_facebook_block_state,
    extract_candidates_with_adaptive_scroll,
    facebook_route_urls,
    normalize_text,
    post_link_count,
    real_post_candidates,
)
from apps.monitoring.services.facebook_session_requests import (
    expire_stale_session_requests,
    mark_session_request_error,
    mark_session_request_running,
    mark_session_request_success,
    next_pending_session_request,
)
from apps.monitoring.services.runner import run_account_checks
from apps.monitoring.services.sync_db import call_sync_db
from apps.monitoring.services.telegram import (
    active_chat_ids,
    send_system_alert,
    send_system_alert_to_chats,
)


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run the human-assisted Facebook session manager loop."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Process one pending request and exit.",
        )
        parser.add_argument(
            "--no-post-check",
            action="store_true",
            help="Do not run Facebook checks after a successful session refresh.",
        )

    def handle(self, *args, **options):
        self.stdout.write("Facebook session manager started.")
        if not os.environ.get("DISPLAY"):
            self.stderr.write(
                "DISPLAY is not set. In Docker, start this command through "
                "scripts/facebook_session_manager.sh."
            )

        while True:
            expire_stale_session_requests()
            request = next_pending_session_request()
            if request:
                self._process_request(
                    request,
                    run_checks_after_success=(
                        settings.FACEBOOK_SESSION_RUN_CHECKS_AFTER_SUCCESS
                        and not options["no_post_check"]
                    ),
                )
                if options["once"]:
                    return
            elif options["once"]:
                self.stdout.write("No pending Facebook session requests.")
                return

            time.sleep(settings.FACEBOOK_SESSION_MANAGER_POLL_SECONDS)

    def _process_request(self, request, *, run_checks_after_success: bool):
        mark_session_request_running(request)
        self.stdout.write(f"Processing Facebook session request #{request.pk}.")
        chat_ids = active_chat_ids()
        credentials = (
            self._load_credentials()
            if settings.FACEBOOK_SESSION_PREFILL_CREDENTIALS
            else ("", "")
        )

        try:
            with sync_playwright() as playwright:
                profile_dir = settings.FACEBOOK_SESSION_BROWSER_PROFILE_DIR
                profile_dir.mkdir(parents=True, exist_ok=True)
                self._clear_stale_profile_locks(profile_dir)
                context = playwright.chromium.launch_persistent_context(
                    str(profile_dir),
                    headless=False,
                    **facebook_base_context_kwargs(),
                )
                try:
                    self._complete_login(context, request, credentials, chat_ids)
                finally:
                    context.close()
        except Exception as exc:
            logger.exception("Facebook session request %s failed", request.pk)
            mark_session_request_error(request, str(exc))
            return

        mark_session_request_success(request)
        if run_checks_after_success:
            self._run_facebook_checks()

    def _clear_stale_profile_locks(self, profile_dir: Path) -> None:
        for lock_name in ("SingletonCookie", "SingletonLock", "SingletonSocket"):
            lock_path = profile_dir / lock_name
            try:
                if lock_path.exists() or lock_path.is_symlink():
                    lock_path.unlink()
            except OSError as exc:
                logger.warning("Could not remove Chromium profile lock %s: %s", lock_path, exc)

    def _complete_login(self, context, request, credentials, chat_ids) -> None:
        page = context.new_page()
        try:
            page.goto(
                "https://www.facebook.com/login",
                wait_until="domcontentloaded",
                timeout=settings.PLAYWRIGHT_TIMEOUT_MS,
            )
            self._try_prefill_credentials(page, credentials, chat_ids)
            self._wait_for_operator_login(page, request, chat_ids)
            page.goto(
                settings.FACEBOOK_AUTH_CHECK_URL,
                wait_until="domcontentloaded",
                timeout=settings.PLAYWRIGHT_TIMEOUT_MS,
            )
            self._wait_for_operator_login(page, request, chat_ids)
            save_facebook_storage_state(context)
            self._validate_monitored_accounts(context, request, chat_ids)
        finally:
            context.close()

    def _load_credentials(self) -> tuple[str, str]:
        try:
            username, password, _credential = get_facebook_login_credentials()
        except FacebookAuthError as exc:
            send_system_alert(
                "Facebook credential could not be loaded",
                [f"Error: {exc}"],
            )
            return "", ""
        return username, password

    def _try_prefill_credentials(self, page, credentials, chat_ids) -> None:
        if not settings.FACEBOOK_SESSION_PREFILL_CREDENTIALS:
            return

        username, password = credentials

        if not username or not password:
            return

        try:
            page.locator('input[name="email"]').fill(username, timeout=10000)
            page.locator('input[name="pass"]').fill(password, timeout=10000)
        except PlaywrightTimeoutError as exc:
            send_system_alert_to_chats(
                "Facebook credential prefill failed",
                [f"Error: {exc}", "Enter the login manually in the session browser."],
                chat_ids,
            )
