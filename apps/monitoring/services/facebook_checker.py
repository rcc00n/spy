import asyncio
import hashlib
import logging
import os
import random
import re
import shutil
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from django.conf import settings
from django.db import close_old_connections
from django.utils.dateparse import parse_datetime
from playwright.sync_api import Page

from apps.monitoring.models import MonitoredAccount
from apps.monitoring.services.facebook_auth import (
    FacebookAuthError,
    authenticated_session_required_message,
    facebook_auth_enabled,
    facebook_base_context_kwargs,
    new_facebook_context,
)
from apps.monitoring.services.text import normalize_text


logger = logging.getLogger(__name__)

FACEBOOK_BASE_URL = "https://www.facebook.com/"
POST_URL_MARKERS = (
    "/posts/",
    "/permalink/",
    "story_fbid=",
    "/photos/",
    "/videos/",
    "/reel/",
)
TRACKING_QUERY_PARAMS = {
    "__cft__",
    "__tn__",
    "ref",
    "refid",
    "mibextid",
    "paipv",
    "eav",
    "fbclid",
    "locale",
}
STABLE_QUERY_PARAMS = {"story_fbid", "id", "fbid", "set", "type", "v"}
UI_TEXT_LINES = {
    "like",
    "comment",
    "share",
    "send",
    "all reactions:",
    "most relevant",
    "top comments",
    "view more comments",
    "see more",
    "see less",
    "log in",
    "sign up",
}
UNAVAILABLE_MARKERS = (
    "this content isn't available",
    "this content is not available",
    "this content isn't available right now",
    "this content is not available right now",
    "this page isn't available",
    "this page is not available",
    "page isn't available",
    "content not found",
    "this account is private",
    "private account",
    "этот контент сейчас недоступен",
    "контент сейчас недоступен",
    "контент недоступен",
    "возможно, владелец удалил контент или ограничил доступ",
    "перейти в справочный центр",
    "закрыл(-а) профиль",
    "закрыл профиль",
    "закрыла профиль",
    "закрытый профиль",
    "только друзья этого человека видят",
    "locked their profile",
    "only friends can see",
    "ce contenu n'est pas disponible",
    "ce contenu est indisponible",
    "este contenido no está disponible",
    "este contenido no esta disponible",
    "este conteúdo não está disponível",
    "este conteudo nao esta disponivel",
    "contenuto non disponibile",
    "dieser inhalt ist derzeit nicht verfügbar",
)
UNAVAILABLE_ONLY_STOPWORDS = {
    "a",
    "an",
    "and",
    "comment",
    "comments",
    "g",
    "г",
    "in",
    "of",
    "on",
    "reply",
    "status",
    "the",
    "updated",
    "write",
    "а",
    "апрель",
    "август",
    "г",
    "года",
    "декабрь",
    "еще",
    "ещё",
    "июль",
    "июнь",
    "комментарий",
    "комментарии",
    "май",
    "март",
    "напишите",
    "ноябрь",
    "обновил",
    "обновила",
    "обновили",
    "октябрь",
    "ответить",
    "сентябрь",
    "свой",
    "статус",
    "февраль",
    "январь",
}
LOW_INFORMATION_STOPWORDS = UNAVAILABLE_ONLY_STOPWORDS | {
    "all",
    "ago",
    "больше",
    "все",
    "мин",
    "назад",
    "нравится",
    "перевод",
    "показать",
    "поделиться",
    "смотреть",
    "ч",
}


@dataclass(frozen=True)
class FacebookPostCandidate:
    external_post_id: str
    post_url: str
    text: str
    published_at: object | None
    raw_snapshot: str
    source_type: str


@dataclass(frozen=True)
class FacebookPageDiagnostics:
    route_url: str
    final_url: str
    page_title: str
    http_status_code: int | None
    facebook_state: str
    article_count: int
    link_count: int
    post_link_count: int
    visible_text: str
    html_snapshot: str
    screenshot_path: str = ""


class FacebookBlocked(RuntimeError):
    def __init__(self, state: str, message: str):
        self.state = state
        super().__init__(message)


def detect_facebook_block_state(
    page_text: str,
    page_url: str = "",
    *,
    has_post_evidence: bool = False,
) -> str:
    text = normalize_text(page_text).lower()
    url = (page_url or "").lower()
    if not text:
        return "empty_response"

    if "checkpoint" in url or any(
        marker in text
        for marker in (
            "captcha",
            "security check",
            "checkpoint",
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
    ) or "two_step_verification" in url:
        return "captcha_or_checkpoint"

    if any(
        marker in text
        for marker in (
            "temporarily blocked",
            "try again later",
            "you can't use this feature right now",
            "we limit how often",
        )
    ):
        return "rate_limited_or_blocked"

    if any(marker in text for marker in UNAVAILABLE_MARKERS):
        if has_post_evidence:
            return "ok"
        return "private_or_unavailable"

    if any(
        marker in text
        for marker in (
            "you must log in",
            "log in to facebook",
            "log into facebook",
            "create an account or log in",
        )
    ):
        return "login_required"

    return "ok"


def is_unavailable_only_candidate_text(
    text: str,
    *,
    account_name: str = "",
) -> bool:
    normalized = normalize_text(text).lower()
    if not any(marker in normalized for marker in UNAVAILABLE_MARKERS):
        return False

    cleaned = normalized
    for marker in sorted(UNAVAILABLE_MARKERS, key=len, reverse=True):
        cleaned = cleaned.replace(marker, " ")

    return len(meaningful_content_tokens(cleaned, account_name=account_name)) < 3


def meaningful_content_tokens(text: str, *, account_name: str = "") -> list[str]:
    account_tokens = set(re.findall(r"[\w]+", normalize_text(account_name).lower()))
    tokens = []
    for token in re.findall(r"[\w]+", normalize_text(text).lower(), flags=re.UNICODE):
        if token.isdigit():
            continue
        if len(token) <= 1:
            continue
        if token in account_tokens:
            continue
        if token in LOW_INFORMATION_STOPWORDS:
            continue
        tokens.append(token)
    return tokens


def is_low_information_candidate_text(
    text: str,
    *,
    account_name: str = "",
) -> bool:
    return len(meaningful_content_tokens(text, account_name=account_name)) < 3


def block_message(state: str) -> str:
    return {
        "login_required": "Facebook public fetch stopped: login wall detected.",
        "captcha_or_checkpoint": "Facebook public fetch stopped: captcha/checkpoint detected.",
        "private_or_unavailable": "Facebook public fetch stopped: private or unavailable content detected.",
        "rate_limited_or_blocked": "Facebook public fetch stopped: rate limit or temporary block detected.",
        "empty_response": "Facebook public fetch stopped: empty public response.",
    }.get(state, "Facebook public fetch stopped.")


def normalize_facebook_url(href: str, base_url: str = FACEBOOK_BASE_URL) -> str:
    if not href:
        return ""
    absolute = urljoin(base_url, href)
    parsed = urlparse(absolute)
    if "facebook.com" not in parsed.netloc.lower():
        return ""

    query_pairs = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key in TRACKING_QUERY_PARAMS:
            continue
        if key in STABLE_QUERY_PARAMS or key.startswith("story_"):
            query_pairs.append((key, value))
    query_pairs.sort()

    path = re.sub(r"/+", "/", parsed.path).rstrip("/") or "/"
    return urlunparse(
        (
            "https",
            "www.facebook.com",
            path,
            "",
            urlencode(query_pairs),
            "",
        )
    )


def is_post_url(url: str) -> bool:
    lowered = url.lower()
    if "facebook.com" not in lowered:
        return False
    if "/reel/" in lowered:
        return bool(re.search(r"/reel/\d+", lowered))
    return any(marker in lowered for marker in POST_URL_MARKERS if marker != "/reel/")


def facebook_account_identity(account_url: str) -> tuple[str, str]:
    parsed = urlparse(normalize_facebook_url(account_url) or account_url)
    path = (parsed.path or "").strip("/")
    if path.lower() == "profile.php":
        query = dict(parse_qsl(parsed.query))
        return ("profile_id", query.get("id", "").lower())
    if path:
        return ("slug", path.split("/", 1)[0].lower())
    return ("", "")


def facebook_post_owner_identity(post_url: str) -> tuple[str, str]:
    parsed = urlparse(normalize_facebook_url(post_url) or post_url)
    path = (parsed.path or "").strip("/")
    lowered_path = path.lower()
    query = dict(parse_qsl(parsed.query))
    if lowered_path in {"permalink.php", "story.php", "photo.php"} and query.get("id"):
        return ("profile_id", query.get("id", "").lower())
    if lowered_path == "profile.php" and query.get("id"):
        return ("profile_id", query.get("id", "").lower())
    if lowered_path.startswith("reel/"):
        return ("unknown", "")
    if path:
        first_segment = path.split("/", 1)[0].lower()
        if first_segment not in {"posts", "photos", "videos", "permalink.php"}:
            return ("slug", first_segment)
    return ("unknown", "")


def facebook_post_url_matches_account(account_url: str, post_url: str) -> bool:
    account_kind, account_value = facebook_account_identity(account_url)
    post_kind, post_value = facebook_post_owner_identity(post_url)
    if not account_kind or not account_value:
        return True
    if post_kind == "unknown" or not post_value:
        return True
    if account_kind != post_kind:
        return True
    return account_kind == post_kind and account_value == post_value


def external_post_id_for(
    account: MonitoredAccount,
    *,
    post_url: str = "",
    text: str = "",
) -> str:
    if post_url:
        source = normalize_facebook_url(post_url) or post_url
        prefix = "facebook-url"
    else:
        source = "|".join([account.platform, str(account.pk or account.account_url), normalize_text(text)])
        prefix = "facebook-text"
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    return f"{prefix}:{digest}"


def clean_post_text(text: str) -> str:
    lines = []
    for raw_line in (text or "").splitlines():
        line = normalize_text(raw_line)
        if not line:
            continue
        if line.lower() in UI_TEXT_LINES:
            continue
        lines.append(line)
    return normalize_text(" ".join(lines))


def parse_visible_timestamp(value: str):
    value = normalize_text(value)
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed:
        return parsed
    return None


def bounded_snapshot(value: str) -> str:
    return (value or "")[: settings.MONITORING_SNAPSHOT_MAX_LENGTH]


def candidate_from_parts(
    account: MonitoredAccount,
    *,
    text: str,
    links: list[str],
    raw_snapshot: str,
    source_type: str,
    timestamp_text: str = "",
    require_post_url: bool = False,
) -> FacebookPostCandidate | None:
    cleaned_text = clean_post_text(text)
    normalized_links = [normalize_facebook_url(link, account.account_url) for link in links]
    post_url = next((link for link in normalized_links if is_post_url(link)), "")

    if require_post_url and not post_url:
        return None

    if len(cleaned_text) < 20 and not post_url:
        return None

    external_post_id = external_post_id_for(account, post_url=post_url, text=cleaned_text)
    snapshot = "\n".join(
        [
            f"source_type={source_type}",
            f"post_url={post_url or account.account_url}",
            "text:",
            cleaned_text,
            "raw:",
            normalize_text(raw_snapshot),
        ]
    )
    return FacebookPostCandidate(
        external_post_id=external_post_id,
        post_url=post_url or account.account_url,
        text=cleaned_text,
        published_at=parse_visible_timestamp(timestamp_text),
        raw_snapshot=bounded_snapshot(snapshot),
        source_type=source_type,
    )


def dedupe_and_limit(
    candidates: list[FacebookPostCandidate],
    limit: int,
) -> list[FacebookPostCandidate]:
    seen = set()
    deduped = []
    for candidate in candidates:
        key = candidate.external_post_id
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
        if len(deduped) >= limit:
            break
    return deduped


def extract_candidates_from_page(
    page: Page,
    account: MonitoredAccount,
    limit: int,
) -> list[FacebookPostCandidate]:
    rows = page.evaluate(
        """
        () => {
          const markers = ['/posts/', '/permalink/', 'story_fbid=', '/photos/', '/videos/', '/reel/'];
          const hasPostLink = (href) => href && markers.some(marker => href.includes(marker));
          const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
          const nodes = new Set();
          document.querySelectorAll('[role="article"], article').forEach(node => nodes.add(node));
          document.querySelectorAll('a[href]').forEach(anchor => {
            if (!hasPostLink(anchor.href || anchor.getAttribute('href'))) return;
            const article = anchor.closest('[role="article"], article');
            nodes.add(article || anchor.closest('div') || anchor.parentElement);
          });
          return Array.from(nodes).filter(Boolean).slice(0, 50).map(node => {
            const links = Array.from(node.querySelectorAll('a[href]')).map(anchor => anchor.href || anchor.getAttribute('href'));
            const timestampNode = node.querySelector('abbr, time, [aria-label]');
            return {
              text: node.innerText || '',
              links,
              html: node.outerHTML || '',
              timestampText: timestampNode ? (timestampNode.getAttribute('aria-label') || timestampNode.getAttribute('title') || timestampNode.getAttribute('datetime') || timestampNode.textContent || '') : '',
              sourceType: node.getAttribute('role') === 'article' || node.tagName.toLowerCase() === 'article' ? 'post_container' : 'permalink_link'
            };
          });
        }
        """
    )

    candidates = []
    for row in rows:
        candidate = candidate_from_parts(
            account,
            text=row.get("text") or "",
            links=row.get("links") or [],
            raw_snapshot=row.get("html") or "",
            source_type=row.get("sourceType") or "post_container",
            timestamp_text=row.get("timestampText") or "",
        )
        if candidate:
            candidates.append(candidate)
    return dedupe_and_limit(candidates, limit)


def _strip_tags(value: str) -> str:
    value = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    return unescape(value)


def _extract_hrefs(value: str) -> list[str]:
    return [
        unescape(match.group(1))
        for match in re.finditer(r"""href=["']([^"']+)["']""", value, flags=re.I)
    ]


def extract_candidates_from_html(
    html: str,
    account: MonitoredAccount,
    limit: int | None = None,
) -> list[FacebookPostCandidate]:
    limit = limit or account.max_posts_per_check
    blocks = []
    article_pattern = re.compile(
        r"""<(?P<tag>article|div)\b[^>]*(?:role=["']article["']|role=article)[^>]*>.*?</(?P=tag)>""",
        re.I | re.S,
    )
    blocks.extend(match.group(0) for match in article_pattern.finditer(html or ""))
    if not blocks:
        link_pattern = re.compile(
            r"""<a\b[^>]*href=["'][^"']*(?:/posts/|/permalink/|story_fbid=|/photos/|/videos/|/reel/)[^"']*["'][^>]*>.*?</a>""",
            re.I | re.S,
        )
        blocks.extend(match.group(0) for match in link_pattern.finditer(html or ""))

    candidates = []
    for block in blocks:
        source_type = "post_container" if "role" in block.lower() else "permalink_link"
        candidate = candidate_from_parts(
            account,
            text=_strip_tags(block),
            links=_extract_hrefs(block),
            raw_snapshot=block,
            source_type=source_type,
        )
        if candidate:
            candidates.append(candidate)

    if not candidates:
        text = _strip_tags(html or "")
        state = detect_facebook_block_state(text)
        if state == "ok":
            candidate = candidate_from_parts(
                account,
                text=text,
                links=_extract_hrefs(html or ""),
                raw_snapshot=html or "",
                source_type="fallback_snapshot",
                require_post_url=True,
            )
            if candidate:
                candidates.append(candidate)
    return dedupe_and_limit(candidates, limit)


def effective_scroll_rounds(configured_rounds: int, limit: int) -> int:
    if limit <= 1:
        return configured_rounds
    return max(configured_rounds, min(5, max(2, limit - 1)))


def scroll_facebook_feed_once(page: Page, round_index: int, total_rounds: int) -> None:
    logger.info("Facebook scroll round %s of %s", round_index, total_rounds)
    page.mouse.wheel(0, random.randint(1600, 2400))
    try:
        page.evaluate(
            """
            () => {
              const amount = Math.max(window.innerHeight || 900, 1200);
              const scrollables = [
                document.scrollingElement,
                ...Array.from(document.querySelectorAll('div')).filter((node) => (
                  node.scrollHeight > node.clientHeight + 300
                )),
              ].filter(Boolean);
              scrollables
                .sort((a, b) => (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight))
                .slice(0, 3)
                .forEach((node) => node.scrollBy(0, amount));
              window.scrollBy(0, amount);
            }
            """
        )
    except Exception as exc:
        logger.debug("Facebook JS scroll helper failed: %s", exc)
    page.wait_for_timeout(random.randint(2200, 4200))


def safe_scroll(page: Page, scroll_rounds: int) -> None:
    for round_index in range(scroll_rounds):
        scroll_facebook_feed_once(page, round_index + 1, scroll_rounds)


def real_post_candidates(
    candidates: list[FacebookPostCandidate],
) -> list[FacebookPostCandidate]:
    return [candidate for candidate in candidates if is_post_url(candidate.post_url)]


def extract_candidates_with_adaptive_scroll(
    page: Page,
    account: MonitoredAccount,
    limit: int,
    scroll_rounds: int,
) -> list[FacebookPostCandidate]:
    target_rounds = effective_scroll_rounds(scroll_rounds, limit)
    collected: list[FacebookPostCandidate] = []

    for round_index in range(target_rounds + 1):
        candidates = real_post_candidates(
            extract_candidates_from_page(page, account, limit * 2)
        )
        collected = dedupe_and_limit([*collected, *candidates], limit)
        logger.info(
            "Facebook candidates after scroll account_id=%s round=%s/%s candidate_count=%s",
            account.pk,
            round_index,
            target_rounds,
            len(collected),
        )
        if len(collected) >= limit or round_index >= target_rounds:
            break
        scroll_facebook_feed_once(page, round_index + 1, target_rounds)

    return collected


def facebook_route_urls(account_url: str) -> list[str]:
    parsed = urlparse(account_url)
    path = (parsed.path or "/").rstrip("/")
    if not path:
        path = "/"
    is_profile_php = path.lower() == "/profile.php"

    routes = [normalize_facebook_url(account_url) or account_url]
    if is_profile_php:
        query = parsed.query
        sk_query = f"{query}&sk=posts" if query else "sk=posts"
        routes.append(urlunparse(("https", "www.facebook.com", "/profile.php", "", sk_query, "")))
        routes.append(urlunparse(("https", "m.facebook.com", "/profile.php", "", query, "")))
        routes.append(urlunparse(("https", "m.facebook.com", "/profile.php", "", sk_query, "")))
        routes.append(urlunparse(("https", "mbasic.facebook.com", "/profile.php", "", query, "")))
        routes.append(urlunparse(("https", "mbasic.facebook.com", "/profile.php", "", sk_query, "")))
        routes.append(urlunparse(("https", "mbasic.facebook.com", "/profile.php", "", f"{query}&v=timeline" if query else "v=timeline", "")))
    elif path != "/":
        routes.append(urlunparse(("https", "www.facebook.com", f"{path}/posts", "", "", "")))
        routes.append(urlunparse(("https", "www.facebook.com", path, "", "sk=posts", "")))
        routes.append(urlunparse(("https", "m.facebook.com", path, "", "", "")))
        routes.append(urlunparse(("https", "m.facebook.com", f"{path}/posts", "", "", "")))
        routes.append(urlunparse(("https", "mbasic.facebook.com", path, "", "", "")))
        routes.append(urlunparse(("https", "mbasic.facebook.com", path, "", "v=timeline", "")))

    deduped = []
    seen = set()
    for route in routes:
        if not route or route in seen:
            continue
        seen.add(route)
        deduped.append(route)
    return deduped


def post_link_count(page: Page) -> tuple[int, int, int]:
    article_count = page.locator("[role='article'], article").count()
    anchors = page.locator("a[href]")
    link_count = anchors.count()
    post_count = 0
    for index in range(min(link_count, 500)):
        href = anchors.nth(index).get_attribute("href") or ""
        normalized_href = normalize_facebook_url(href, page.url) or href
        if is_post_url(normalized_href):
            post_count += 1
    return article_count, link_count, post_count


def diagnostics_dir() -> Path:
    return settings.BASE_DIR / "runtime" / "check_diagnostics"


def collect_page_diagnostics(
    page: Page,
    *,
    route_url: str,
    response,
    state: str,
    visible_text: str,
    run=None,
    attempt_index: int = 0,
) -> FacebookPageDiagnostics:
    article_count, link_count, post_count = post_link_count(page)
    screenshot_path = ""
    if run and getattr(run, "pk", None):
        path = diagnostics_dir() / f"check_run_{run.pk}_attempt_{attempt_index}.png"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(path), full_page=True, timeout=10000)
            screenshot_path = str(path)
        except Exception as exc:
            logger.warning("Could not save Facebook diagnostic screenshot: %s", exc)
    try:
        html_snapshot = page.content()
    except Exception:
        html_snapshot = ""
    return FacebookPageDiagnostics(
        route_url=route_url,
        final_url=page.url,
        page_title=page.title(),
        http_status_code=response.status if response else None,
        facebook_state=state,
        article_count=article_count,
        link_count=link_count,
        post_link_count=post_count,
        visible_text=visible_text,
        html_snapshot=html_snapshot,
        screenshot_path=screenshot_path,
    )


def diagnostics_score(diagnostics: FacebookPageDiagnostics | None) -> tuple[int, int, int]:
    if not diagnostics:
        return (-1, -1, -1)
    state_score = 1 if diagnostics.facebook_state == "ok" else 0
    return (state_score, diagnostics.post_link_count, diagnostics.article_count)


def update_check_run_diagnostics(run, diagnostics: FacebookPageDiagnostics | None) -> None:
    if not run or not diagnostics:
        return
    values = {
        "final_url": diagnostics.final_url[:2000],
        "route_url": diagnostics.route_url[:2000],
        "page_title": diagnostics.page_title[:500],
        "http_status_code": diagnostics.http_status_code,
        "facebook_state": diagnostics.facebook_state[:100],
        "article_count": diagnostics.article_count,
        "link_count": diagnostics.link_count,
        "post_link_count": diagnostics.post_link_count,
        "diagnostic_text": bounded_snapshot(diagnostics.visible_text),
        "diagnostic_html_snapshot": bounded_snapshot(diagnostics.html_snapshot),
        "screenshot_path": diagnostics.screenshot_path[:1000],
    }

    def update() -> None:
        close_old_connections()
        run.__class__.objects.filter(pk=run.pk).update(**values)
        for field, value in values.items():
            setattr(run, field, value)
        close_old_connections()

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        update()
        return

    result = {"error": None}

    def runner() -> None:
        try:
            update()
        except Exception as exc:
            result["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if result["error"]:
        raise result["error"]


def copy_browser_profile(source_dir: Path) -> tempfile.TemporaryDirectory | None:
    if not source_dir.exists():
        return None
    temp_dir = tempfile.TemporaryDirectory()
    target = Path(temp_dir.name) / "profile"

    def ignore(_dir, names):
        return [
            name
            for name in names
            if name.startswith("Singleton") or name.endswith(".lock")
        ]

    try:
        shutil.copytree(source_dir, target, ignore=ignore)
    except Exception:
        temp_dir.cleanup()
        raise
    return temp_dir

