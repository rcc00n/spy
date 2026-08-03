import time
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, SimpleTestCase, TransactionTestCase
from django.urls import reverse
from playwright.sync_api import sync_playwright

from apps.research.forms import FacebookDiscoverySourceForm, FacebookScanForm
from apps.research.models import FacebookDiscoverySource, FacebookDiscoveryRun, ResearchJob
from apps.research.services.facebook_targets import canonical_source_url, public_group_header
from apps.research.services.facebook_discovery import initialize_discovery, discover_feed, run_next_discovery, belongs_to_source
from apps.research.facebook_tasks import complete_if_idle


class SourceUrlTests(SimpleTestCase):
    def test_only_explicit_facebook_source_urls_are_accepted(self):
        self.assertEqual(canonical_source_url('https://m.facebook.com/PCLconstruction/?locale=ru_RU'),'https://www.facebook.com/PCLconstruction/')
        self.assertEqual(canonical_source_url('https://www.facebook.com/groups/123/?sorting_setting=CHRONOLOGICAL','group'),'https://www.facebook.com/groups/123/')
        for url in ['http://facebook.com/PCLconstruction','https://facebook.com.evil.test/PCLconstruction',
                    'https://user:pass@facebook.com/PCLconstruction','https://facebook.com:8080/PCLconstruction',
                    'https://facebook.com:bad/PCLconstruction','https://facebook.com/me/',
                    'https://facebook.com/search/posts/?q=PCL','https://facebook.com/foo/posts/123/']:
            self.assertEqual(canonical_source_url(url),'')
        self.assertEqual(canonical_source_url('https://facebook.com/PCLconstruction/','group'),'')
        self.assertEqual(canonical_source_url('https://facebook.com/groups/123/','page'),'')

    def test_recommendation_and_other_group_links_do_not_belong_to_source(self):
        self.assertTrue(belongs_to_source('https://www.facebook.com/PCLconstruction/posts/123/','https://www.facebook.com/PCLconstruction/'))
        self.assertFalse(belongs_to_source('https://www.facebook.com/other/posts/123/','https://www.facebook.com/PCLconstruction/'))
        self.assertFalse(belongs_to_source('https://www.facebook.com/groups/1234/posts/1/','https://www.facebook.com/groups/123/'))


class DiscoveryPortalTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('operator',is_staff=True)
        self.client.force_login(self.user)
        self.source=FacebookDiscoverySource.objects.create(name='PCL',kind='page',target='https://www.facebook.com/PCLconstruction/')

    @patch('apps.research.views.run_facebook_scan.delay',return_value=SimpleNamespace(id='discovery-task'))
    def test_watchlist_only_scan_snapshots_source_and_rejects_disabled_sources(self,delay):
        response=self.client.post(reverse('research:facebook_scan_create'),{'sources':[self.source.pk],'depth':'quick'},secure=True)
        self.assertEqual(response.status_code,302)
        job=ResearchJob.objects.get();run=job.facebook_discoveries.get()
        self.source.target='https://www.facebook.com/changed/'
        self.source.enabled=False;self.source.save()
        run.refresh_from_db()
        self.assertEqual(run.target,'https://www.facebook.com/PCLconstruction/')
        self.assertEqual(job.plan['discovery_version'],1)
        invalid=FacebookScanForm(data={'sources':[self.source.pk],'depth':'quick'})
        self.assertFalse(invalid.is_valid())
        self.assertEqual(self.client.get(reverse('research:facebook_sources'),secure=True).status_code,200)
        self.assertContains(self.client.get(reverse('research:job_detail',args=[job.pk]),secure=True),'Discovery sources')

    def test_staff_permissions_and_no_get_mutation(self):
        count=FacebookDiscoverySource.objects.count()
        self.client.get(reverse('research:facebook_source_create'),secure=True)
        self.assertEqual(FacebookDiscoverySource.objects.count(),count)
        self.user.is_staff=False;self.user.save()
        for name,args in [('facebook_sources',[]),('facebook_source_create',[]),('facebook_source_edit',[self.source.pk])]:
            self.assertEqual(self.client.get(reverse('research:'+name,args=args),secure=True).status_code,403)

    def test_source_type_validation_and_duplicate_normalization(self):
        form=FacebookDiscoverySourceForm(data={'name':'Wrong','kind':'group','target':self.source.target,'enabled':True})
        self.assertFalse(form.is_valid())
        duplicate=FacebookDiscoverySourceForm(data={'name':'Duplicate','kind':'page','target':self.source.target+'?locale=ru_RU','enabled':True})
        self.assertFalse(duplicate.is_valid())

    def test_initialize_deduplicates_source_plus_manual_and_empty_form_fails(self):
        job=ResearchJob.objects.create(query='Watchlist scan')
        initialize_discovery(job,[self.source],[self.source.target,'PCL Oilers'])
        initialize_discovery(job,[self.source],[self.source.target,'PCL Oilers'])
        self.assertEqual(job.facebook_discoveries.count(),2)
        self.assertFalse(FacebookScanForm(data={'depth':'quick'}).is_valid())

    def test_pending_or_failed_discovery_cannot_be_reported_as_completed(self):
        job=ResearchJob.objects.create(query='PCL',status='social_collecting')
        initialize_discovery(job,[self.source],[])
        complete_if_idle(job)
        self.assertFalse(job.is_terminal)
        job.facebook_discoveries.update(status='failed',error='No feed')
        complete_if_idle(job)
        self.assertEqual(job.status,'failed')


class FeedDomTests(SimpleTestCase):
    def test_feed_keeps_owned_posts_and_reels_but_excludes_recommendations(self):
        html='''<main role="main"><h1>PCL</h1>
        <article role="article"><a href="https://www.facebook.com/PCLconstruction/">PCL</a><a href="https://www.facebook.com/reel/123/">Reel</a></article>
        <a href="https://www.facebook.com/PCLconstruction/posts/456/">Post</a>
        <article role="article"><a href="https://www.facebook.com/other/">Other</a><a href="https://www.facebook.com/reel/999/">Other reel</a></article>
        <a href="https://www.facebook.com/other/posts/111/">Recommended post</a></main>'''
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            try:
                page=browser.new_page()
                page.route('**/*',lambda r:r.fulfill(status=200,content_type='text/html',body=html))
                batches=[]
                result=discover_feed(page,'https://www.facebook.com/PCLconstruction/','page',10,0,time.monotonic()+15,batches.append)
            finally:browser.close()
        self.assertEqual(set(batches[0]),{'https://www.facebook.com/reel/123/','https://www.facebook.com/PCLconstruction/posts/456/'})
        self.assertEqual(result['error'],'')

    def test_public_group_header_ignores_post_claims_of_public_visibility(self):
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            try:
                page=browser.new_page()
                page.set_content('<main role="main"><header><h1>Group</h1><div>Public group</div></header><div role="feed"><article role="article">Private group</article></div></main>')
                self.assertTrue(public_group_header(page))
                page.set_content('<main role="main"><header><h1>Group</h1><div>Private group</div></header><div role="feed"><article role="article">Public group</article></div></main>')
                self.assertFalse(public_group_header(page))
                page.set_content('<main role="main"><header><h1>Group</h1></header><div role="feed"><article role="article">Public group</article></div></main>')
                self.assertFalse(public_group_header(page))
            finally:browser.close()

    def test_private_group_is_not_discovered_or_joined(self):
        html='<main role="main"><header><h1>Group</h1><div>Private group</div></header><button onclick="window.joined=true">Join</button><article role="article"><a href="https://www.facebook.com/groups/123/posts/456/">Post</a></article></main>'
        with sync_playwright() as p:
