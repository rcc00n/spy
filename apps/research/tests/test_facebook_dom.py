import time

from django.test import SimpleTestCase
from playwright.sync_api import sync_playwright

from apps.research.services.facebook_browser import collect_thread


class DiscussionDomTests(SimpleTestCase):
    def test_removing_hidden_dialog_does_not_break_public_thread_or_mix_background(self):
        url = 'https://www.facebook.com/example/posts/123/'
        html = '''<html><body>
        <article role="article" aria-label="Comment by Background"><div dir="auto">Wrong background</div>
        <a href="https://www.facebook.com/example/posts/123/?comment_id=999">time</a></article>
        <div role="dialog" id="loading" style="display:none">Loading</div>
        <div role="dialog"><span aria-label="Shared with Public"></span>
        <div data-ad-preview="message">PCL construction post</div>
        <article role="article" aria-label="Comment by Person"><div dir="auto">Good work PCL</div>
        <a href="https://www.facebook.com/example/posts/123/?comment_id=42">time</a></article>
        <button aria-label="Ещё" onclick="window.wrongMenuClicked=true;document.body.insertAdjacentHTML('beforeend','<div role=dialog>Unrelated link information</div>')"></button>
        <button onclick="document.getElementById('loading').remove(); this.remove()">See more</button>
        </div></body></html>'''
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html', body=html))
                saved = []
                result = collect_thread(page, url, 1, time.monotonic() + 20, checkpoint=saved.append)
                self.assertFalse(page.evaluate('!!window.wrongMenuClicked'))
            finally:
                browser.close()
        self.assertTrue(result['public_verified'])
        self.assertEqual(result['coverage'], 'comment_limit_reached')
        self.assertEqual([c['id'] for c in result['comments']], ['42'])
        self.assertEqual(saved[0]['text'], 'PCL construction post')
        self.assertEqual(saved[-1]['comments'][0]['text'], 'Good work PCL')


from unittest.mock import patch
from django.test import TransactionTestCase
from apps.research.models import ResearchJob, FacebookComment
from apps.research.facebook_tasks import scan_step
from apps.research.services.facebook_store import enqueue_url


class RealBrowserCheckpointTests(TransactionTestCase):
    def test_real_playwright_event_loop_commits_to_database(self):
        url = 'https://www.facebook.com/example/posts/123/'
        job = ResearchJob.objects.create(query=url, plan={'engine': 'facebook_browser', 'collector_version': 2, 'discovery_index': 1})
        work = enqueue_url(job, url)
        html = '''<div role="dialog"><span aria-label="Shared with Public"></span>
        <div data-ad-preview="message">PCL public post</div>
        <article role="article" aria-label="Comment by Person"><div dir="auto">Saved PCL comment</div>
        <a href="https://www.facebook.com/example/posts/123/?comment_id=42">time</a></article></div>'''

        def collect_fixture(page, source_url, limit, deadline, **kwargs):
            page.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html', body=html))
            return collect_thread(page, source_url, 1, deadline, **kwargs)

