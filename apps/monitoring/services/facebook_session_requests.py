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
        token_hint=raw_token[-8:],
        expires_at=expires_at,
    )
    request.operator_url = build_operator_url(request, raw_token)
    request.save(update_fields=["operator_url", "updated_at"])
    send_session_request_alert(request)
    return request, raw_token


def build_operator_url(request: FacebookSessionRefreshRequest, raw_token: str) -> str:
    base_url = settings.FACEBOOK_SESSION_MANAGER_URL
    if not base_url:
        return ""
    parts = urlsplit(base_url)
    query = dict(parse_qsl(parts.query))
    query.update({"request": request.pk, "token": raw_token, "autoconnect": "true", "resize": "scale"})
    return urlunsplit(parts._replace(query=urlencode(query)))


def verify_request_token(
    request: FacebookSessionRefreshRequest,
    raw_token: str,
) -> bool:
    return bool(raw_token) and secrets.compare_digest(
