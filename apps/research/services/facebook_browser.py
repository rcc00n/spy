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
        result['coverage'] = 'discussion_container_not_found'
        return result
    labels = scope.locator('[aria-label], [title], svg title').evaluate_all('(ns) => ns.flatMap(n => [n.getAttribute("aria-label"), n.getAttribute("title"), n.tagName.toLowerCase() === "title" ? n.textContent : ""])')
    group_public = group_public and group_from_post(page.url) == group
    if not any(PUBLIC.search(x or '') for x in labels) and not group_public:
        result['coverage'] = 'public_visibility_not_verified'
        return result
    result['public_verified'] = True
    # Expand only known read-only controls; never click Reply, Like or Publish.
    # An icon labelled 'Ещё' can open an unrelated 'About this content' dialog.
    # Only expand controls with actual visible caption text, never icon menus.
    more_text = re.compile(r'^(See more|Ещё|Еще)$', re.I)
    more = scope.get_by_role('button', name=more_text).filter(has_text=more_text)
    if more.count() and more.first.is_visible():
        more.first.click(timeout=3000)
    # Keep a bounded context snapshot without comment bodies or account chrome.
    result['text'] = scope.evaluate(r'''(root) => {
        const message = root.querySelector('[data-ad-preview="message"], [data-ad-comet-preview="message"]');
        if (message) return message.innerText || '';
        const articles = Array.from(root.querySelectorAll('[role="article"]'));
        const post = articles.find(n => !/^(Comment by|Reply by|Комментарий|Ответ)/i.test(n.getAttribute('aria-label') || ''));
        if (post) {
            const copy = post.cloneNode(true);
            copy.querySelectorAll('[role="article"], [role="button"], button, input, textarea, [contenteditable]').forEach(n => n.remove());
            const value = copy.innerText || copy.textContent || '';
            if (value.trim()) return value;
        }
        if (root.getAttribute('role') === 'dialog') {
            let text = root.innerText || '';
            articles.forEach(n => { text = text.replace(n.innerText || '', ''); });
            return text;
        }
        const badge = Array.from(root.querySelectorAll('[aria-label], [title]')).find(n => /Shared with Public|Поделился.*Доступно всем/.test(n.getAttribute('aria-label') || n.getAttribute('title') || ''));
        let node = badge;
        for (let i=0; node && i<18; i++, node=node.parentElement) {
            if (node.querySelector('[role=article]')) break;
            const text = node.innerText || '';
            if (text.length > 50) return text;
        }
        return '';
    }''')[:14000]
    result['mentions_pcl'] = bool(TARGET.search(result['text']))
    result['coverage'] = 'collecting'
    if checkpoint:
        checkpoint(dict(result))
    if limit == 0:
        result['coverage'] = 'post_only_comments_not_requested'
        return result
    sort = scope.get_by_role('button', name=SORT)
    if sort.count():
        sort.first.click(timeout=3000)
        items = page.get_by_role('menuitem')
        chosen = False
        choices = [(r'^(All comments|Все комментарии)', 'all'), (r'^(Newest|Most recent|Новые|Сначала новые)', 'newest')]
        for pattern, label in (list(reversed(choices)) if prefer_newest else choices):
            option = items.filter(has_text=re.compile(pattern, re.I))
            if option.count():
                option.first.click(timeout=3000)
                result['sort'] = label
                chosen = True
                page.wait_for_timeout(1200)
                break
        if not chosen:
            page.keyboard.press('Escape')
            result['sort'] = 'relevance_filtered'
    comments = {}
    observed_rows = 0
    clicks = 0
    stagnant = 0
    end = min(deadline, time.monotonic() + seconds)
    result['coverage'] = 'visible_comments_only'
    while time.monotonic() < end:
        check_page(page)
        previous_count = len(comments)
        batch = []
        rows = scope.evaluate(COMMENT_ROWS)
        observed_rows += len(rows)
        for row in rows:
            record = comment_record(row, url)
            if record:
                if comments.get(record['id']) != record:
                    batch.append(record)
                comments[record['id']] = record
        if checkpoint and batch:
            checkpoint({**result, 'comments': batch, 'coverage': 'collecting'})
        stagnant = stagnant + 1 if len(comments) == previous_count else 0
        if len(comments) >= limit:
            result['coverage'] = 'comment_limit_reached'
            break
        expander = scope.get_by_role('button', name=EXPAND)
        visible = next((expander.nth(i) for i in range(min(expander.count(), 20)) if expander.nth(i).is_visible()), None)
        if visible is None:
            if stagnant >= 3:
                result['coverage'] = 'no_new_comments_after_scroll'
                break
            # Newest comments often load by scrolling, without a More button.
            scope.evaluate(r'''root => {
                const nodes = [root, ...root.querySelectorAll('*')].filter(n =>
                    n.clientHeight > 100 && n.scrollHeight > n.clientHeight + 100 &&
                    /auto|scroll/.test(getComputedStyle(n).overflowY)
                );
                nodes.sort((a,b) => (b.scrollHeight-b.clientHeight)-(a.scrollHeight-a.clientHeight));
                nodes.slice(0,2).forEach(n => n.scrollBy(0, Math.max(n.clientHeight, 700)));
            }''')
            page.wait_for_timeout(1500)
            continue
        if clicks >= expansion_limit:
            result['coverage'] = 'expansion_limit_reached'
            break
        visible.click(timeout=3000)
        clicks += 1
        page.wait_for_timeout(900)
    else:
        result['coverage'] = 'time_budget_reached'
    if not comments and result['coverage'] == 'no_new_comments_after_scroll':
        result['coverage'] = 'comment_links_not_matched' if observed_rows else 'comments_not_observed'
    result['comments'] = list(comments.values())
    result['video_analysis'] = 'not_performed'
    return result


def browser_scan(queries, depth='standard', progress=None):
    post_limit, rounds, visit_limit, comment_limit = {
        'quick': (5, 2, 6, 0), 'standard': (10, 4, 12, 40), 'deep': (20, 7, 24, 100)
    }.get(depth, (10, 4, 12, 40))
    kwargs = facebook_context_kwargs(auth_enabled=True)
    result = {'queries': [], 'sources': [], 'discovered_urls': [], 'observed_at': datetime.now(timezone.utc).isoformat(),
              'limitations': 'Bounded personalized Facebook search, not exhaustive. Only confirmed public content is stored. Dates, video/audio and images are not analyzed. Captured text may be automatically translated by Facebook. No sentiment or factual truth is inferred from keyword matches.'}
    deadline = time.monotonic() + 600
    found = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(**kwargs)
        page = context.new_page()
        try:
            for query in queries:
                if time.monotonic() >= deadline:
                    break
                entry = {'query': query, 'status': 'ok', 'discovered': 0}
                try:
                    direct = canonical_post_url(query)
                    urls = [direct] if direct else discover(page, query, post_limit, rounds, deadline)
                    entry['discovered'] = len(urls)
                    for url in urls:
                        found.setdefault(url, []).append(query)
                except FacebookAccessStopped as exc:
                    entry.update(status='error', error=str(exc)[:500])
                    result['queries'].append(entry)
                    raise
                except Exception as exc:
                    entry.update(status='error', error=str(exc)[:500])
                result['queries'].append(entry)
                if progress:
                    progress(f"Search: {query} — {entry['discovered']} links; {entry['status']}")
            # Share the visit budget across queries, so one broad query cannot
            # consume all visits before a narrow reputational query is checked.
            by_query = [[url for url, terms in found.items() if query in terms] for query in queries]
            ordered = list(dict.fromkeys(url for index in range(post_limit) for urls in by_query for url in urls[index:index + 1]))
            result['discovered_urls'] = ordered
            for url in ordered[:visit_limit]:
                if time.monotonic() >= deadline:
                    break
                try:
                    source = collect_thread(page, url, comment_limit, deadline)
                except FacebookAccessStopped:
                    raise
                except Exception as exc:
                    source = {'url': url, 'text': '', 'comments': [], 'public_verified': False, 'coverage': 'error', 'error': str(exc)[:500]}
