import logging
import re

from apps.monitoring.models import TelegramMessageTemplate
from apps.monitoring.services.sync_db import call_sync_db


logger = logging.getLogger(__name__)

PLACEHOLDER_RE = re.compile(r"{([a-zA-Z_][a-zA-Z0-9_]*)}")


