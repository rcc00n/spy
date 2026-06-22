import hashlib
import secrets
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.conf import settings
from django.utils import timezone

from apps.monitoring.models import FacebookSessionRefreshRequest
from apps.monitoring.services.facebook_auth import storage_state_path
from apps.monitoring.services.telegram import send_system_alert


def token_hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def create_facebook_session_request(*, user=None) -> tuple[FacebookSessionRefreshRequest, str]:
    raw_token = secrets.token_urlsafe(32)
    expires_at = timezone.now() + timezone.timedelta(
        seconds=settings.FACEBOOK_SESSION_REQUEST_TTL_SECONDS
    )
    request = FacebookSessionRefreshRequest.objects.create(
        requested_by=user if getattr(user, "is_authenticated", False) else None,
        token_hash=token_hash(raw_token),
