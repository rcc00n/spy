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
        request.token_hash,
        token_hash(raw_token),
    )


def expire_stale_session_requests() -> int:
    now = timezone.now()
    stale = FacebookSessionRefreshRequest.objects.filter(
        status__in=[
            FacebookSessionRefreshRequest.Status.PENDING,
            FacebookSessionRefreshRequest.Status.RUNNING,
        ],
        expires_at__lte=now,
    )
    expired = 0
    for request in stale:
        request.status = FacebookSessionRefreshRequest.Status.EXPIRED
        request.finished_at = now
        request.error_message = "The operator did not complete Facebook login before the request expired."
        request.save(update_fields=["status", "finished_at", "error_message", "updated_at"])
        send_session_result_alert(request)
        expired += 1
    return expired


def next_pending_session_request() -> FacebookSessionRefreshRequest | None:
    expire_stale_session_requests()
    return (
        FacebookSessionRefreshRequest.objects.filter(
            status=FacebookSessionRefreshRequest.Status.PENDING,
            expires_at__gt=timezone.now(),
        )
        .order_by("requested_at")
        .first()
    )


def mark_session_request_running(request: FacebookSessionRefreshRequest) -> None:
    request.status = FacebookSessionRefreshRequest.Status.RUNNING
    request.started_at = timezone.now()
    request.error_message = ""
    request.save(update_fields=["status", "started_at", "error_message", "updated_at"])
    send_system_alert(
        "Facebook session refresh started",
        [
            f"Request: #{request.pk}",
            f"Expires: {request.expires_at:%Y-%m-%d %H:%M:%S %Z}",
            "Open the session-manager browser and complete Facebook login manually.",
        ],
    )


def mark_session_request_success(request: FacebookSessionRefreshRequest) -> None:
    now = timezone.now()
    request.status = FacebookSessionRefreshRequest.Status.SUCCESS
    request.finished_at = now
    request.error_message = ""
    request.session_path = str(storage_state_path())
    request.created_session = storage_state_path().exists()
    request.save(
