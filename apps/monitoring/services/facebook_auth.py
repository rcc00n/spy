import logging
import os
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from django.utils import timezone
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from apps.monitoring.services.text import normalize_text


logger = logging.getLogger(__name__)

CHECKPOINT_MARKERS = (
    "captcha",
    "checkpoint",
    "security check",
    "two-step verification",
    "two step verification",
    "two-factor authentication",
    "two factor authentication",
