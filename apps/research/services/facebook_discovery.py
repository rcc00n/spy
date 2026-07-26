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
