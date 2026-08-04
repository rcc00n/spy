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
            browser=p.chromium.launch(headless=True)
            try:
                page=browser.new_page();page.route('**/*',lambda r:r.fulfill(status=200,content_type='text/html',body=html))
                found=[]
                result=discover_feed(page,'https://www.facebook.com/groups/123/','group',10,0,time.monotonic()+15,found.append)
                self.assertFalse(page.evaluate('!!window.joined'))
            finally:browser.close()
        self.assertEqual(found,[])
        self.assertEqual(result['coverage'],'public_group_not_verified')


class DiscoveryCheckpointTests(TransactionTestCase):
    def test_real_browser_checkpoint_deduplicates_posts_and_retains_both_origins(self):
        job=ResearchJob.objects.create(query='Watchlist scan',depth='quick',plan={'engine':'facebook_browser','collector_version':2,'discovery_version':1})
        a=FacebookDiscoveryRun.objects.create(job=job,kind='page',name='PCL',target='https://www.facebook.com/PCLconstruction/')
        b=FacebookDiscoveryRun.objects.create(job=job,kind='search',name='Search PCL',target='PCL Oilers')
        html='<main role="main"><article role="article"><a href="https://www.facebook.com/PCLconstruction/posts/123/">PCL post</a></article></main>'
        real=discover_feed
        def feed(page,*args):
            page.route('**/*',lambda r:r.fulfill(status=200,content_type='text/html',body=html))
            # One real browser pass, then an injected failure AFTER commit.
            result=real(page,args[0],args[1],10,0,time.monotonic()+15,args[-1])
            raise RuntimeError('Interrupted after finding a link')
        def search(page,query,limit,rounds,deadline,on_urls):
            on_urls(['https://www.facebook.com/PCLconstruction/posts/123/'])
        with patch('apps.research.services.facebook_discovery.facebook_context_kwargs',return_value={}),patch('apps.research.services.facebook_discovery.discover_feed',side_effect=feed):
            run_next_discovery(job)
        a.refresh_from_db()
        self.assertEqual(a.status,'queued')
        self.assertEqual(a.posts.count(),1)
        self.assertEqual(job.facebook_threads.count(),1)
        with patch('apps.research.services.facebook_discovery.facebook_context_kwargs',return_value={}),patch('apps.research.services.facebook_discovery.discover',side_effect=search):
            run_next_discovery(job)
        b.refresh_from_db()
        self.assertEqual(b.posts.count(),1, b.error)
        self.assertEqual(job.facebook_threads.count(),1)
        self.assertEqual(len(job.facebook_threads.get().queries),2)


class GroupPostDomTests(SimpleTestCase):
    def test_group_post_requires_fresh_public_header_before_reading(self):
        from apps.research.services.facebook_browser import collect_thread
        post='https://www.facebook.com/groups/123/posts/456/'
        group='https://www.facebook.com/groups/123/'
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            try:
                for visibility in ['Public group','Private group']:
                    page=browser.new_page();visited=[]
                    def route(r):
                        visited.append(r.request.url)
                        if r.request.url==group:
                            html=f'<main role="main"><header><h1>Group</h1><div>{visibility}</div></header></main>'
                        else:
                            html=f'<div role="dialog"><div data-ad-preview="message">Public group discussion</div><article role="article" aria-label="Comment by Person"><div dir="auto">PCL comment</div><a href="{post}?comment_id=7">time</a></article></div>'
                        r.fulfill(status=200,content_type='text/html',body=html)
                    page.route('**/*',route)
                    result=collect_thread(page,post,1,time.monotonic()+20)
                    if visibility=='Public group':
                        self.assertEqual(result['comments'][0]['id'],'7')
                        self.assertTrue(result['public_verified'])
                    else:
                        self.assertNotIn(post,visited)
                        self.assertFalse(result['public_verified'])
                        self.assertEqual(result['coverage'],'public_group_not_verified')
                    page.close()
            finally:browser.close()


class SeedTests(TestCase):
    def test_seed_is_idempotent_and_does_not_enable_disabled_sources_or_start_jobs(self):
        from django.core.management import call_command
        from io import StringIO
        call_command('seed_facebook_sources',stdout=StringIO())
        count=FacebookDiscoverySource.objects.count()
        source=FacebookDiscoverySource.objects.first();source.enabled=False;source.save()
        call_command('seed_facebook_sources',stdout=StringIO())
        source.refresh_from_db()
        self.assertFalse(source.enabled)
        self.assertEqual(FacebookDiscoverySource.objects.count(),count)
        self.assertEqual(ResearchJob.objects.count(),0)


class ContinuationTests(TestCase):
