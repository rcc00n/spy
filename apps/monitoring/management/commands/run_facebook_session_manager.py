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
