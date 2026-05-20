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
/runs [limit] - show latest check runs.
/session - show Facebook session status.
/refreshsession - create a human-assisted Facebook session refresh request.
"""


class BotCommandError(ValueError):
    pass


def command_tail(context) -> str:
    return " ".join(context.args or []).strip()


def parse_limit(value: str | None, *, default: int = 5, maximum: int = 20) -> int:
    if not value:
        return default
    try:
        limit = int(value)
    except ValueError as exc:
        raise BotCommandError("Limit must be a number.") from exc
    if not 1 <= limit <= maximum:
        raise BotCommandError(f"Limit must be between 1 and {maximum}.")
    return limit


def normalize_keyword_phrase(value: str) -> str:
    phrase = " ".join((value or "").split())
    if not phrase:
        raise BotCommandError("Keyword phrase is required.")
    return phrase


def keyword_label(keyword: Keyword) -> str:
    active = "active" if keyword.is_active else "paused"
    return f"#{keyword.pk} {active}: {keyword.phrase}"
