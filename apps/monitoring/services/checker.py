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
                    "Skipping Facebook candidate for account_id=%s because it is unavailable-only",
                    account.pk,
                )
                record_check_run_post(
                    run,
                    candidate,
                    sequence=sequence,
                    status=CheckRunPost.Status.SKIPPED,
                    skip_reason="unavailable_only",
                )
                continue
            if is_low_information_candidate_text(
                candidate.text,
                account_name=account.account_name,
            ):
                logger.warning(
                    "Skipping Facebook candidate for account_id=%s because it has low information text",
                    account.pk,
                )
                record_check_run_post(
                    run,
                    candidate,
                    sequence=sequence,
                    status=CheckRunPost.Status.SKIPPED,
                    skip_reason="low_information_text",
                )
                continue
            state = detect_facebook_block_state(
                candidate.text,
                candidate.post_url,
                has_post_evidence=is_post_url(candidate.post_url),
            )
            if state != "ok":
                logger.warning(
                    "Skipping Facebook candidate for account_id=%s because state=%s",
                    account.pk,
                    state,
                )
                record_check_run_post(
                    run,
                    candidate,
                    sequence=sequence,
                    status=CheckRunPost.Status.SKIPPED,
                    skip_reason=state,
                )
                continue
        posts_found += 1
        post, post_created = Post.objects.get_or_create(
            platform=account.platform,
            external_post_id=candidate.external_post_id,
            defaults={
                "monitored_account": account,
                "post_url": candidate.post_url or account.account_url,
                "text": candidate.text,
                "published_at": candidate.published_at,
                "raw_snapshot": candidate.raw_snapshot,
            },
        )

        if post_created:
            new_posts_found += 1
        else:
            update_fields = []
            if post.monitored_account_id != account.pk:
                post.monitored_account = account
                update_fields.append("monitored_account")
            if post.post_url != (candidate.post_url or account.account_url):
                post.post_url = candidate.post_url or account.account_url
                update_fields.append("post_url")
            if post.text != candidate.text:
                post.text = candidate.text
                update_fields.append("text")
            if candidate.published_at and post.published_at != candidate.published_at:
                post.published_at = candidate.published_at
                update_fields.append("published_at")
            if post.raw_snapshot != candidate.raw_snapshot:
                post.raw_snapshot = candidate.raw_snapshot
                update_fields.append("raw_snapshot")
            if update_fields:
                post.save(update_fields=update_fields)

        record_check_run_post(
            run,
            candidate,
            sequence=sequence,
            status=CheckRunPost.Status.STORED,
            post=post,
            is_new=post_created,
        )

        for keyword, preview in keyword_matches(candidate.text, active_keywords):
            match, match_created = PostKeywordMatch.objects.get_or_create(
                post=post,
                keyword=keyword,
                defaults={"matched_text_preview": preview},
            )
            if match_created:
                matches_created += 1
                alert_match_ids.append(match.pk)

    return posts_found, new_posts_found, matches_created, alert_match_ids


def record_check_run_post(
    run: CheckRun | None,
    candidate: FacebookPostCandidate,
    *,
    sequence: int,
    status: str,
    post: Post | None = None,
    is_new: bool = False,
    skip_reason: str = "",
) -> None:
    if not run:
        return

    CheckRunPost.objects.update_or_create(
        check_run=run,
        sequence=sequence,
        defaults={
            "post": post,
            "status": status,
            "skip_reason": bounded(skip_reason, 255),
            "external_post_id": bounded(candidate.external_post_id, 255),
            "post_url": candidate.post_url,
            "source_type": bounded(candidate.source_type, 100),
            "text": candidate.text,
            "raw_snapshot": candidate.raw_snapshot,
            "is_new": is_new,
            "observed_at": timezone.now(),
        },
    )


def run_account_check(
    account: MonitoredAccount,
    *,
    send_telegram: bool = True,
    post_limit: int | None = None,
) -> CheckRun:
    run = CheckRun.objects.create(
        monitored_account=account,
        status=CheckRun.Status.RUNNING,
    )
    try:
        logger.info(
            "Starting account check id=%s name=%s url=%s limit=%s",
            account.pk,
            account.account_name,
            account.account_url,
            post_limit or account.max_posts_per_check,
        )
        candidates = fetch_account_candidates(account, post_limit=post_limit, run=run)
        posts_found, new_posts_found, matches_found, alert_match_ids = (
            store_post_candidates(account, candidates, run=run)
        )
        logger.info(
            "Finished account check id=%s candidates=%s posts=%s new=%s matches=%s",
            account.pk,
            len(candidates),
            posts_found,
            new_posts_found,
            matches_found,
        )
        if send_telegram and alert_match_ids:
            matches_to_alert = PostKeywordMatch.objects.select_related(
                "keyword",
                "post",
                "post__monitored_account",
            ).filter(pk__in=alert_match_ids)
            for match in matches_to_alert:
                send_match_alert(match)
    except PlaywrightTimeoutError as exc:
        _finish_error(run, account, f"Timed out loading public page: {exc}")
        send_check_run_summary(run, account, send_telegram=send_telegram)
    except FacebookBlocked as exc:
        if exc.state in {"login_required", "captcha_or_checkpoint"}:
            _finish_auth_required(run, account, str(exc))
        else:
            _finish_error(run, account, str(exc))
        send_check_run_summary(run, account, send_telegram=send_telegram)
    except PublicFetchBlocked as exc:
        _finish_error(run, account, str(exc))
        send_check_run_summary(run, account, send_telegram=send_telegram)
    except Exception as exc:
        logger.exception("Monitoring check failed for account %s", account.pk)
        _finish_error(run, account, str(exc))
        send_check_run_summary(run, account, send_telegram=send_telegram)
    else:
        now = timezone.now()
        run.status = CheckRun.Status.SUCCESS
        run.finished_at = now
        run.posts_found = posts_found
        run.new_posts_found = new_posts_found
        run.matches_found = matches_found
        run.save(
            update_fields=[
                "status",
                "finished_at",
                "posts_found",
                "new_posts_found",
                "matches_found",
            ]
        )
        account.last_checked_at = now
        account.last_status = CheckRun.Status.SUCCESS
        account.last_error = ""
        account.save(update_fields=["last_checked_at", "last_status", "last_error"])
        send_check_run_summary(run, account, send_telegram=send_telegram)
    return run


def send_check_run_summary(
    run: CheckRun,
    account: MonitoredAccount,
    *,
    send_telegram: bool,
) -> None:
    if not send_telegram or not settings.TELEGRAM_SEND_CHECK_RUN_SUMMARY:
        return

    observed_count = run.observed_posts.count()
    lines = [
        f"Account: {account.account_name}",
        f"Platform: {account.get_platform_display()}",
        f"Status: {run.status}",
        f"Posts found: {run.posts_found}",
        f"New posts: {run.new_posts_found}",
        f"Matches: {run.matches_found}",
        f"Observed candidates: {observed_count}",
    ]
    if run.error_message:
        lines.append(f"Error: {run.error_message[:700]}")
    if run.facebook_state:
        lines.append(f"Facebook state: {run.facebook_state}")
    if run.route_url:
        lines.append(f"Route: {run.route_url}")
    if run.final_url:
        lines.append(f"Final URL: {run.final_url}")
    if run.article_count or run.post_link_count:
        lines.append(
            f"DOM: articles={run.article_count}, post_links={run.post_link_count}"
        )
    if run.screenshot_path:
        lines.append(f"Screenshot: {run.screenshot_path}")

    try:
        send_system_alert(f"Check run #{run.pk} {run.status}", lines)
    except Exception:
        logger.exception("Failed sending check run summary for run %s", run.pk)


def _finish_error(run: CheckRun, account: MonitoredAccount, message: str) -> None:
    now = timezone.now()
    run.status = CheckRun.Status.ERROR
    run.finished_at = now
    run.error_message = bounded(message, 4000)
    run.save(update_fields=["status", "finished_at", "error_message"])

    account.last_checked_at = now
    account.last_status = CheckRun.Status.ERROR
    account.last_error = run.error_message
    account.save(update_fields=["last_checked_at", "last_status", "last_error"])


def _finish_auth_required(run: CheckRun, account: MonitoredAccount, message: str) -> None:
    now = timezone.now()
    run.status = CheckRun.Status.AUTH_REQUIRED
    run.finished_at = now
    run.error_message = bounded(message, 4000)
    run.save(update_fields=["status", "finished_at", "error_message"])

    account.last_checked_at = now
    account.last_status = CheckRun.Status.AUTH_REQUIRED
    account.last_error = run.error_message
    account.save(update_fields=["last_checked_at", "last_status", "last_error"])
