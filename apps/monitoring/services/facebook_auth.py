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
    if settings.FACEBOOK_LOGIN_EMAIL and settings.FACEBOOK_LOGIN_PASSWORD:
        return True
    if storage_state_path().exists():
        return True

    try:
        credential = get_stored_facebook_credential()
    except Exception as exc:
        logger.debug("Could not inspect stored Facebook credentials: %s", exc)
        return False

    return bool(
        credential
        and credential.username
        and credential.password_configured
    )


def storage_state_path() -> Path:
    return Path(settings.FACEBOOK_AUTH_STORAGE_STATE_PATH)


def facebook_base_context_kwargs() -> dict:
    return {
        "user_agent": settings.MONITORING_USER_AGENT,
        "viewport": {"width": 1366, "height": 900},
        "locale": "en-US",
    }


def facebook_context_kwargs(*, auth_enabled: bool | None = None) -> dict:
    kwargs = facebook_base_context_kwargs()
    auth_enabled = facebook_auth_enabled() if auth_enabled is None else auth_enabled
    if not auth_enabled:
        return kwargs

