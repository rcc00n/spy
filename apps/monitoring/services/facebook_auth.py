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
    "authentication code",
    "login code",
    "approve your login",
    "check your notifications",
    "confirm you're not a robot",
    "confirm you are not a robot",
)
LOGIN_MARKERS = (
    "email or phone",
    "forgot password",
    "you must log in",
    "log in to facebook",
    "log into facebook",
    "create an account or log in",
)


class FacebookAuthError(RuntimeError):
    pass


def facebook_auth_enabled() -> bool:
    if bool(settings.FACEBOOK_AUTH_ENABLED):
        return True
