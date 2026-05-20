import logging

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.monitoring.models import (
    CheckRun,
    FacebookSessionRefreshRequest,
    Keyword,
    PlatformCredential,
    PostKeywordMatch,
    TelegramChat,
)
from apps.monitoring.services.facebook_auth import storage_state_path
from apps.monitoring.services.facebook_session_requests import (
    create_facebook_session_request,
)
from apps.monitoring.services.runner import (
    accounts_for_check,
    format_check_summary,
    run_account_checks,
)
from apps.monitoring.services.telegram_templates import render_telegram_template


logger = logging.getLogger(__name__)

HELP_TEXT = """Commands:
/help - show this help.
/keywords - list keywords with IDs.
/addkeyword <phrase> - add or reactivate a keyword.
/pausekeyword <id|phrase> - deactivate a keyword.
/resumekeyword <id|phrase> - reactivate a keyword.
/check [account_id] [limit] - run checks now. Limit must be 1-20.
/matches [limit] - show latest keyword matches.
