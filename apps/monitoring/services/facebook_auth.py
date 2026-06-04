import logging
import time
from pathlib import Path

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

    state_path = storage_state_path()
    if not state_path.exists():
        raise FacebookAuthError(
            "Facebook auth is enabled but the Facebook session file does not exist. "
            "Run `python manage.py refresh_facebook_session`, or add active "
            "Facebook credentials in the admin panel so the checker can refresh it."
        )
    kwargs["storage_state"] = str(state_path)
    return kwargs


def new_facebook_context(
    browser,
    *,
    auth_enabled: bool | None = None,
    username: str = "",
    password: str = "",
):
    return browser.new_context(**facebook_context_kwargs(auth_enabled=auth_enabled))


def authenticated_session_required_message() -> str:
    return (
        "Facebook authenticated session is missing, expired, or was rejected. "
        "Refresh it with `python manage.py refresh_facebook_session`; the checker "
        "will not solve CAPTCHA/checkpoint challenges."
    )


def visible_text(page) -> str:
    try:
        return normalize_text(page.locator("body").inner_text(timeout=5000))
    except Exception:
        try:
            return normalize_text(
                page.evaluate("document.body ? document.body.innerText : ''")
            )
        except Exception:
            return ""


def facebook_session_state(page) -> str:
    text = visible_text(page).lower()
    url = (page.url or "").lower()

    if (
        "checkpoint" in url
        or "two_step_verification" in url
        or any(marker in text for marker in CHECKPOINT_MARKERS)
    ):
        return "checkpoint"
    if "/login" in url or any(marker in text for marker in LOGIN_MARKERS):
        return "login_required"
    return "authenticated"


def get_stored_facebook_credential():
    from apps.monitoring.models import PlatformCredential

    return (
        PlatformCredential.objects.filter(
            platform=PlatformCredential.Platform.FACEBOOK,
            is_active=True,
        )
        .order_by("-updated_at")
        .first()
    )


def get_facebook_login_credentials() -> tuple[str, str, object | None]:
    if settings.FACEBOOK_LOGIN_EMAIL and settings.FACEBOOK_LOGIN_PASSWORD:
        return settings.FACEBOOK_LOGIN_EMAIL, settings.FACEBOOK_LOGIN_PASSWORD, None

    credential = get_stored_facebook_credential()
    if not credential:
        return "", "", None

    try:
        password = credential.get_password()
    except Exception as exc:
        credential.last_error = str(exc)
        credential.save(update_fields=["last_error", "updated_at"])
        raise FacebookAuthError(str(exc)) from exc
    return credential.username, password, credential


def record_facebook_credential_refresh_success(credential_id: int | None) -> None:
    if not credential_id:
        return

    from apps.monitoring.models import PlatformCredential

    now = timezone.now()
    PlatformCredential.objects.filter(pk=credential_id).update(
        last_session_refreshed_at=now,
        last_error="",
        updated_at=now,
    )


def record_facebook_credential_refresh_error(
    credential_id: int | None,
    error_message: str,
) -> None:
    if not credential_id:
        return

    from apps.monitoring.models import PlatformCredential

    PlatformCredential.objects.filter(pk=credential_id).update(
        last_error=error_message,
        updated_at=timezone.now(),
    )


def submit_facebook_login(page, username: str, password: str) -> None:
    page.locator('input[name="email"]').fill(username, timeout=10000)
    page.locator('input[name="pass"]').fill(password, timeout=10000)
    try:
        page.locator(
            'button[name="login"], button[type="submit"], input[type="submit"]'
        ).first.click(timeout=5000)
    except PlaywrightTimeoutError:
        page.locator('input[name="pass"]').press("Enter", timeout=5000)
    try:
        page.wait_for_load_state("domcontentloaded", timeout=10000)
    except PlaywrightTimeoutError:
        pass


def wait_for_facebook_authentication(page, *, timeout_ms: int) -> None:
    deadline = time.monotonic() + (timeout_ms / 1000)
    last_state = "unknown"

    while time.monotonic() < deadline:
        last_state = facebook_session_state(page)
        if last_state == "authenticated":
            return
        if last_state == "checkpoint":
            raise FacebookAuthError(
                "Facebook login requires two-step verification, checkpoint, or "
                "another interactive approval. Complete that manually and refresh "
                "the saved browser session; the checker will not bypass it."
            )
        page.wait_for_timeout(1000)

    raise FacebookAuthError(
        f"Facebook login did not complete before timeout; last state={last_state}."
    )


def refresh_facebook_storage_state_with_credentials(
    browser,
    *,
    username: str,
    password: str,
    timeout_ms: int | None = None,
) -> Path:
    if not username or not password:
        raise FacebookAuthError(
            "No Facebook login credentials are configured. Add active Facebook "
            "credentials in the admin panel or set FACEBOOK_LOGIN_EMAIL and "
            "FACEBOOK_LOGIN_PASSWORD."
        )

    timeout_ms = timeout_ms or (settings.FACEBOOK_LOGIN_TIMEOUT_SECONDS * 1000)
    context = browser.new_context(**facebook_base_context_kwargs())
    page = context.new_page()
    try:
        page.goto(
            "https://www.facebook.com/login",
            wait_until="domcontentloaded",
            timeout=settings.PLAYWRIGHT_TIMEOUT_MS,
        )
        submit_facebook_login(page, username, password)
        wait_for_facebook_authentication(page, timeout_ms=timeout_ms)
        page.goto(
            settings.FACEBOOK_AUTH_CHECK_URL,
            wait_until="domcontentloaded",
            timeout=settings.PLAYWRIGHT_TIMEOUT_MS,
        )
        wait_for_facebook_authentication(page, timeout_ms=timeout_ms)
        state_path = save_facebook_storage_state(context)
        return state_path
    finally:
        context.close()


def ensure_facebook_storage_state(
    browser,
    *,
    auth_enabled: bool | None = None,
    username: str = "",
    password: str = "",
) -> None:
    auth_enabled = facebook_auth_enabled() if auth_enabled is None else auth_enabled
    if not auth_enabled:
        return
    if storage_state_path().exists():
        return

    logger.info("Facebook storage state missing; attempting credential login refresh.")
    refresh_facebook_storage_state_with_credentials(
        browser,
        username=username,
        password=password,
    )


def save_facebook_storage_state(context) -> Path:
    state_path = storage_state_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(state_path))
    try:
        state_path.chmod(0o600)
    except OSError:
        logger.warning("Could not restrict permissions for %s", state_path)
    return state_path
