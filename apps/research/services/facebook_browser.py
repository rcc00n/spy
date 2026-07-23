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


PUBLIC = re.compile(r'(?:Shared with\s*Public|Поделился.*Доступно всем|^Public$|^Доступно всем$)', re.I)


def canonical_post_url(value):
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or parsed.hostname not in {'facebook.com', 'www.facebook.com', 'm.facebook.com'}:
        return ''
    path = parsed.path.rstrip('/')
    query = parse_qs(parsed.query)
    if re.search(r'/(?:posts|reel|videos|permalink)/[^/]+', path):
        return urlunsplit(('https', 'www.facebook.com', path + '/', '', ''))
    if path in {'/story.php', '/permalink.php'} and query.get('story_fbid') and query.get('id'):
        return 'https://www.facebook.com' + path + '?' + urlencode({'story_fbid': query['story_fbid'][0], 'id': query['id'][0]})
    return ''


def comment_record(row, source_url):
    """Reject comment links from recommendations/other posts or profile links."""
    for link in row.get('links', []):
        if canonical_post_url(link) != source_url:
            continue
        query = parse_qs(urlsplit(link).query)
        parent_id = (query.get('comment_id') or [''])[0]
