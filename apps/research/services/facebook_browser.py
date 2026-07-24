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
        reply_id = (query.get('reply_comment_id') or [''])[0]
        key = reply_id or parent_id
        if not key:
            continue
        text = '\n'.join(dict.fromkeys(x.strip() for x in row.get('texts', []) if x.strip()))[:12000]
        params = {'comment_id': parent_id}
        if reply_id:
            params['reply_comment_id'] = reply_id
        separator = '&' if '?' in source_url else '?'
        return {'id': key, 'parent_id': parent_id if reply_id else '', 'url': source_url + separator + urlencode(params),
                'text': text, 'has_media': bool(row.get('has_media')), 'mentions_pcl': bool(TARGET.search(text))}
    return None


def check_page(page):
    text = page.locator('body').inner_text(timeout=5000)
    state = detect_facebook_block_state(text, page.url, has_post_evidence=page.locator('[role=article]').count() > 0)
    if state in {'login_required', 'captcha_or_checkpoint', 'rate_limited_or_blocked'}:
        raise FacebookAccessStopped('Facebook access state: ' + state)
    if state != 'ok':
        raise RuntimeError('Facebook access state: ' + state)
    return text


def discover(page, query, limit, rounds, deadline, on_urls=None):
    url = 'https://www.facebook.com/search/posts/?' + urlencode({'q': query})
    response = page.goto(url, wait_until='domcontentloaded', timeout=30000)
    if response and response.status >= 400:
        raise RuntimeError(f'Facebook search HTTP {response.status}')
    page.wait_for_timeout(2500)
    seen = {}
    stagnant = 0
    for _ in range(rounds + 1):
        check_page(page)
        before = len(seen)
        links = page.locator('[role=article] a[href]').evaluate_all('(nodes) => nodes.map(n => n.href)')
        for link in links:
            post = canonical_post_url(link)
            if post:
                seen[post] = None
        if on_urls and len(seen) != before:
            on_urls(list(seen)[:limit])
        stagnant = stagnant + 1 if len(seen) == before else 0
        if len(seen) >= limit or stagnant >= 2 or time.monotonic() >= deadline:
            break
        page.mouse.wheel(0, 1800)
        page.wait_for_timeout(1500)
    return list(seen)[:limit]


COMMENT_ROWS = r'''(root) => Array.from(root.querySelectorAll('[role="article"]')).filter(n =>
    /^(Comment by|Reply by|Комментарий|Ответ)/i.test(n.getAttribute('aria-label') || '')
).map(n => {
    const own = x => x.closest('[role="article"]') === n;
    const textNodes = Array.from(n.querySelectorAll('[dir="auto"]')).filter(x =>
        own(x) && !x.closest('a, [role="button"], button') &&
        !Array.from(x.querySelectorAll('[dir="auto"]')).some(y => own(y))
    );
    return {
        texts: textNodes.map(x => x.innerText || '').filter(Boolean),
        links: Array.from(n.querySelectorAll('a[href]')).filter(own).map(a => a.href),
        has_media: Array.from(n.querySelectorAll('img,video')).filter(own).some(x => (x.alt || '').length > 0)
    };
})'''


def collect_thread(page, url, limit, deadline, checkpoint=None, seconds=60, expansion_limit=25, prefer_newest=False):
    result = {'url': url, 'text': '', 'comments': [], 'public_verified': False,
              'coverage': 'not_read', 'sort': 'unknown', 'mentions_pcl': False}
    group = group_from_post(url)
    group_public = False
    if group:
        page.goto(group, wait_until='domcontentloaded', timeout=30000)
        page.wait_for_timeout(2500)
        check_page(page)
        group_public = canonical_source_url(page.url, 'group') == group and public_group_header(page)
        if not group_public:
            result['coverage'] = 'public_group_not_verified'
            return result
    response = page.goto(url, wait_until='domcontentloaded', timeout=30000)
    if response and response.status >= 400:
        result['coverage'] = f'http_{response.status}'
        return result
    page.wait_for_timeout(2000)
    check_page(page)
    # Reels initially hide their public badge, caption and comments together.
    if '/reel/' in url and not page.locator('[role=article]').count():
        opener = page.get_by_role('button', name=OPEN_COMMENTS)
        if opener.count():
            opener.first.click(timeout=5000)
            page.wait_for_timeout(1500)
    # A post can open in a dialog over the user's unrelated home feed.
    # Never collect that background feed or use its visibility badge.
    # Resolve the last *currently visible* dialog on each operation. Absolute nth
    # indices become invalid when Facebook removes a loading/hidden dialog.
    dialogs = page.locator('[role="dialog"]:visible')
    scope = dialogs.last if dialogs.count() else None
    if scope is None and '/reel/' in url:
        scope = page.locator('body')
    if scope is None:
        articles = page.locator('[role=article]')
        for i in range(min(articles.count(), 50)):
            article = articles.nth(i)
            links = article.locator('a[href]').evaluate_all('(ns) => ns.map(n => n.href)')
            if any(canonical_post_url(link) == url for link in links):
                scope = article
                break
    if scope is None:
