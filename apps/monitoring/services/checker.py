import hashlib
import logging
import random
import time
from dataclasses import dataclass

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from apps.monitoring.models import (
    CheckRun,
    CheckRunPost,
    Keyword,
    MonitoredAccount,
    Post,
    PostKeywordMatch,
)
from apps.monitoring.services.facebook_checker import (
    FacebookBlocked,
    FacebookPostCandidate,
    detect_facebook_block_state,
    extract_public_facebook_posts,
    facebook_post_url_matches_account,
    is_low_information_candidate_text,
    is_post_url,
    is_unavailable_only_candidate_text,
)
from apps.monitoring.services.telegram import send_match_alert, send_system_alert
from apps.monitoring.services.text import keyword_matches, normalize_text


logger = logging.getLogger(__name__)

PROTECTION_MARKERS = (
    "confirm you're not a robot",
    "confirm you are not a robot",
    "captcha",
    "temporarily blocked",
    "checkpoint",
    "this account is private",
    "private account",
)


@dataclass
class FetchedPage:
    final_url: str
    visible_text: str
    raw_snapshot: str
    status_code: int | None = None


class PublicFetchBlocked(RuntimeError):
    pass


def bounded(value: str, max_length: int) -> str:
    return (value or "")[:max_length]


def random_delay() -> None:
    min_delay = settings.MONITORING_MIN_DELAY_SECONDS
    max_delay = settings.MONITORING_MAX_DELAY_SECONDS
    if max_delay <= 0:
        return
    if min_delay > max_delay:
        min_delay, max_delay = max_delay, min_delay
    time.sleep(random.uniform(min_delay, max_delay))


def fetch_public_page(url: str) -> FetchedPage:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=settings.MONITORING_USER_AGENT,
            viewport={"width": 1366, "height": 900},
            locale="en-US",
        )
        page = context.new_page()
        try:
            response = page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=settings.PLAYWRIGHT_TIMEOUT_MS,
            )
            page.wait_for_timeout(random.randint(1200, 3000))
            try:
                visible_text = page.locator("body").inner_text(timeout=5000)
            except Exception:
                visible_text = page.evaluate("document.body ? document.body.innerText : ''")
            html_snapshot = page.content()
            status_code = response.status if response else None
            final_url = page.url
        finally:
            context.close()
            browser.close()

    normalized_text = normalize_text(visible_text)
    lowered = normalized_text.lower()
    if status_code and status_code >= 400:
        raise PublicFetchBlocked(
            f"Public fetch returned HTTP {status_code}; no bypass attempted."
        )
    if any(marker in lowered for marker in PROTECTION_MARKERS):
        raise PublicFetchBlocked(
            "Public fetch stopped because the page appears protected or private."
        )

    return FetchedPage(
        final_url=final_url,
        visible_text=bounded(normalized_text, settings.MONITORING_TEXT_MAX_LENGTH),
        raw_snapshot=bounded(
            "\n".join(
                [
                    f"status={status_code}",
                    f"url={final_url}",
                    "visible_text:",
                    normalized_text,
                    "html_snapshot:",
                    html_snapshot,
                ]
            ),
            settings.MONITORING_SNAPSHOT_MAX_LENGTH,
        ),
        status_code=status_code,
    )


def check_facebook_account(account: MonitoredAccount) -> FetchedPage:
    logger.info("Checking public Facebook account %s", account.account_url)
    return fetch_public_page(account.account_url)


def check_instagram_account(account: MonitoredAccount) -> FetchedPage:
    logger.info("Checking public Instagram account %s", account.account_url)
    return fetch_public_page(account.account_url)


def fetch_account(account: MonitoredAccount) -> FetchedPage:
    if account.platform == MonitoredAccount.Platform.FACEBOOK:
        return check_facebook_account(account)
    if account.platform == MonitoredAccount.Platform.INSTAGRAM:
        return check_instagram_account(account)
    raise ValueError(f"Unsupported platform: {account.platform}")


def fetch_account_candidates(
    account: MonitoredAccount,
    *,
    post_limit: int | None = None,
    run: CheckRun | None = None,
) -> list[FacebookPostCandidate]:
    if account.platform == MonitoredAccount.Platform.FACEBOOK:
        return extract_public_facebook_posts(account, post_limit=post_limit, run=run)

    fetched = fetch_account(account)
    return [
        FacebookPostCandidate(
            external_post_id=external_id_for(account, fetched),
            post_url=fetched.final_url or account.account_url,
            text=fetched.visible_text,
            published_at=None,
            raw_snapshot=fetched.raw_snapshot,
            source_type="fallback_snapshot",
        )
    ]


def external_id_for(account: MonitoredAccount, fetched: FetchedPage) -> str:
    digest_source = "|".join(
        [
            account.platform,
            account.account_url,
            normalize_text(fetched.visible_text),
        ]
    )
    return hashlib.sha256(digest_source.encode("utf-8")).hexdigest()


@transaction.atomic
def store_post_candidates(
    account: MonitoredAccount,
    candidates: list[FacebookPostCandidate],
    *,
    run: CheckRun | None = None,
) -> tuple[int, int, int, list[int]]:
    if not candidates:
        return 0, 0, 0, []

    active_keywords = list(Keyword.objects.filter(is_active=True))
    posts_found = 0
    new_posts_found = 0
    matches_created = 0
    alert_match_ids = []

    for sequence, candidate in enumerate(candidates, start=1):
        if not candidate.text:
            record_check_run_post(
                run,
                candidate,
                sequence=sequence,
                status=CheckRunPost.Status.SKIPPED,
                skip_reason="empty_text",
            )
            continue
        if account.platform == MonitoredAccount.Platform.FACEBOOK:
            if not is_post_url(candidate.post_url):
                logger.warning(
                    "Skipping Facebook candidate for account_id=%s because it has no post URL",
                    account.pk,
                )
                record_check_run_post(
                    run,
                    candidate,
                    sequence=sequence,
                    status=CheckRunPost.Status.SKIPPED,
                    skip_reason="missing_post_url",
                )
                continue
            if not facebook_post_url_matches_account(account.account_url, candidate.post_url):
                logger.warning(
                    "Skipping Facebook candidate for account_id=%s because it belongs to another account",
                    account.pk,
                )
                record_check_run_post(
                    run,
                    candidate,
                    sequence=sequence,
                    status=CheckRunPost.Status.SKIPPED,
                    skip_reason="external_account_post",
                )
                continue
            if is_unavailable_only_candidate_text(
                candidate.text,
                account_name=account.account_name,
            ):
                logger.warning(
