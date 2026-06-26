import logging
import re

from apps.monitoring.models import TelegramMessageTemplate
from apps.monitoring.services.sync_db import call_sync_db


logger = logging.getLogger(__name__)

PLACEHOLDER_RE = re.compile(r"{([a-zA-Z_][a-zA-Z0-9_]*)}")


def render_telegram_template(
    key: str,
    *,
    context: dict | None = None,
    default: str = "",
) -> str:
    body = default
    try:
        configured_body = call_sync_db(
            lambda: TelegramMessageTemplate.objects.filter(key=key, is_active=True)
            .values_list("body", flat=True)
            .first()
        )
        if configured_body:
            body = configured_body
    except Exception:
        logger.exception("Could not load Telegram message template key=%s", key)

    values = {name: "" if value is None else str(value) for name, value in (context or {}).items()}

    def replace(match) -> str:
        placeholder = match.group(1)
        return values.get(placeholder, match.group(0))

    try:
        return PLACEHOLDER_RE.sub(replace, body).strip()
    except Exception:
        logger.exception("Could not render Telegram message template key=%s", key)
        return default.strip()
