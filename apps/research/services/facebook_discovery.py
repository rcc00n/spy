"""Independent search and source-feed discovery with durable provenance."""
import re
import time
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

from django.utils import timezone
from playwright.sync_api import sync_playwright

from apps.monitoring.services.facebook_auth import facebook_context_kwargs
from apps.monitoring.services.sync_db import call_sync_db
from apps.research.models import FacebookDiscoveryRun, ResearchEvent
from apps.research.services.facebook_browser import canonical_post_url, check_page, discover, FacebookAccessStopped
from apps.research.services.facebook_targets import canonical_source_url, public_group_header
from apps.research.services.facebook_store import enqueue_url


def initialize_discovery(job, sources, manual_lines):
    for source in sources:
        FacebookDiscoveryRun.objects.get_or_create(job=job,kind=source.kind,target=source.target,
            defaults={'source':source,'name':source.name})
    for line in manual_lines:
        direct=canonical_post_url(line)
        target=canonical_source_url(line) if not direct else ''
        kind='post' if direct else ('group' if '/groups/' in target else 'page') if target else 'search'
        FacebookDiscoveryRun.objects.get_or_create(job=job,kind=kind,target=direct or target or line,defaults={'name':line[:160]})


def belongs_to_source(post, source):
    pp=urlsplit(post); sp=urlsplit(source)
    if sp.path.startswith('/groups/'):
        return pp.path.startswith(sp.path.rstrip('/')+'/')
    if sp.path == '/profile.php':
        owner=parse_qs(sp.query).get('id',[''])[0]
        return pp.path.startswith('/'+owner+'/') or parse_qs(pp.query).get('id',[''])[0] == owner
    owner=sp.path.strip('/').lower()
    return pp.path.lower().startswith('/'+owner+'/')


FEED_CARDS = '''root => Array.from(root.querySelectorAll('[role="article"]')).filter(n=>
    !/^(Comment by|Reply by|Комментарий|Ответ)/i.test(n.getAttribute('aria-label')||'')
).map(n=>({links:Array.from(n.querySelectorAll('a[href]')).filter(a=>a.closest('[role="article"]')===n).map(a=>a.href)}))'''


def discover_feed(page, target, kind, limit, rounds, deadline, on_urls):
    response=page.goto(target,wait_until='domcontentloaded',timeout=30000)
    if response and response.status >= 400:
        return {'coverage':f'http_{response.status}','error':f'HTTP {response.status}'}
    page.wait_for_timeout(3000)
    check_page(page)
    landed=canonical_source_url(page.url,kind)
    if not landed:
        return {'coverage':'source_redirect_not_verified','error':'Source redirected outside a page/group feed.'}
    if kind == 'group' and not public_group_header(page):
        return {'coverage':'public_group_not_verified','error':'Public group header not verified; no group links collected.'}
    main=page.locator('[role="main"]').last
    if not main.count():
        return {'coverage':'feed_not_found','error':'Facebook feed container not found.'}
    found={}; stagnant=0; coverage='scroll_budget_reached'
    for _ in range(rounds+1):
        check_page(page)
        before=len(found)
        # Owner-scoped URLs avoid importing posts from unrelated recommendations.
        links=main.locator('a[href]').evaluate_all('(ns)=>ns.map(n=>n.href)')
        for link in links:
            post=canonical_post_url(link)
            if post and belongs_to_source(post,landed):found[post]=None
        # Generic /reel/<id> links need an owner link in the same post card.
        for card in main.evaluate(FEED_CARDS):
            if any(canonical_source_url(link)==landed for link in card['links']):
                for link in card['links']:
                    post=canonical_post_url(link)
                    if post and '/reel/' in post:found[post]=None
        if len(found)!=before:on_urls(list(found)[:limit])
        stagnant=stagnant+1 if len(found)==before else 0
        if len(found)>=limit:
            coverage='link_limit_reached';break
        if time.monotonic()>=deadline:
            coverage='time_budget_reached';break
        if stagnant>=3:
            coverage='no_new_links_observed' if found else 'no_links_observed';break
        page.mouse.wheel(0,1700);page.wait_for_timeout(1500)
    return {'coverage':coverage,'error':'' if found else 'No source-owned post links were exposed.'}


def run_next_discovery(job):
    run=job.facebook_discoveries.filter(status__in=['queued','running'],next_attempt_at__lte=timezone.now()).first()
    if run is None:return False
    run.status='running';run.attempts+=1;run.cycle_attempts+=1;run.error='';run.save()
    if run.cycle_attempts>3:
        run.status='failed';run.coverage='worker_interrupted';run.error='Discovery interrupted repeatedly; continue to retry.';run.save();return True
    limit,rounds={'quick':(5,2),'standard':(10,4),'deep':(20,7)}.get(job.depth,(10,4))
    found=set();result={}
    def save_urls(urls):
        found.update(urls)
        def save():
            for url in urls:
                work=enqueue_url(job,url,[f'{run.kind}: {run.name}'])
                if work:run.posts.add(work.post_id)
        call_sync_db(save)
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            try:
                page=browser.new_page(**facebook_context_kwargs(auth_enabled=True))
                page.set_default_timeout(10000)
                if run.kind=='post':
                    save_urls([run.target]);result={'coverage':'direct_link','error':''}
                elif run.kind=='search':
                    discover(page,run.target,limit,rounds,time.monotonic()+90,on_urls=save_urls)
                    result={'coverage':'bounded_search' if found else 'no_links_observed','error':'' if found else 'Search exposed no post links.'}
                else:
                    result=discover_feed(page,run.target,run.kind,limit,rounds,time.monotonic()+90,save_urls)
            finally:browser.close()
    except FacebookAccessStopped as exc:
        run.status='blocked';run.error=str(exc)[:1000];run.coverage='access_stopped';run.save();raise
    except Exception as exc:
        result={'coverage':'error','error':str(exc)[:1000]}
    run.coverage=result['coverage'];run.error=result.get('error','')
    run.status=('queued' if run.cycle_attempts<3 else 'failed') if run.error else 'done'
