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

