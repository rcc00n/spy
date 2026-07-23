"""Bounded, read-only Facebook discovery and public discussion collection.

Uses the operator's existing browser session. Never submits forms or reacts.
Missing public markers, login challenges and inaccessible content remain gaps.
"""
import re
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from playwright.sync_api import sync_playwright

from apps.monitoring.services.facebook_auth import facebook_context_kwargs
from apps.monitoring.services.facebook_checker import detect_facebook_block_state
from apps.research.services.facebook_targets import group_from_post, public_group_header, canonical_source_url

COMMENT_LABEL = re.compile(r'^(?:Comment by|Reply by|Комментарий|Ответ)', re.I)
OPEN_COMMENTS = re.compile(r'^(?:Comment|Comments|Leave a comment|Оставить комментарий|Комментировать)$', re.I)
SORT = re.compile(r'^(?:Most relevant|All comments|Newest|Most recent|Самые актуальные|Все комментарии|Новые|Сначала новые)\s*\ufeff?$', re.I)
EXPAND = re.compile(r'^(?:View (?:\d+ (?:more )?)?(?:more |previous )?(?:comments?|repl(?:y|ies))|See more comments|Показать (?:ещ[её] )?(?:\d+ )?комментар|Посмотреть (?:ещ[её] )?(?:\d+[\s\xa0]*)?ответ|Показать (?:ещ[её] )?(?:\d+[\s\xa0]*)?ответ)', re.I)
TARGET = re.compile(r'\bPCL\b|PCL\s*(?:Construction|Constructors|Industrial|Energy)', re.I)
class FacebookAccessStopped(RuntimeError):
    pass

