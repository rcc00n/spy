import asyncio
import logging
import threading

from django.conf import settings

from apps.monitoring.models import TelegramChat
from apps.monitoring.services.telegram_templates import render_telegram_template


logger = logging.getLogger(__name__)


def run_telegram_coroutine(coro) -> int:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result = {"sent": 0, "error": None}

    def runner():
        try:
            result["sent"] = asyncio.run(coro)
        except Exception as exc:
            result["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if result["error"]:
        raise result["error"]
    return result["sent"]


def active_chat_ids() -> list[int]:
