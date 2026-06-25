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
    return list(TelegramChat.objects.filter(is_active=True).values_list("chat_id", flat=True))


def send_telegram_message_to_chats(
    text: str,
    chat_ids: list[int],
    *,
    disable_web_page_preview: bool = True,
) -> int:
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        logger.info("TELEGRAM_BOT_TOKEN is not configured; skipping Telegram message.")
        return 0

    if not chat_ids:
        logger.info("No active Telegram chats are registered; skipping Telegram message.")
        return 0

    try:
        from telegram import Bot
    except ImportError:
        logger.exception("python-telegram-bot is not installed; skipping Telegram message.")
        return 0

