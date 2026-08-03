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
