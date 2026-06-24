import asyncio
import logging
import threading

from django.conf import settings

from apps.monitoring.models import TelegramChat
from apps.monitoring.services.telegram_templates import render_telegram_template


logger = logging.getLogger(__name__)

