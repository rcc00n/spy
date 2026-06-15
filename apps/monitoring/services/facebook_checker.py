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
