from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from apps.research.forms import FacebookScanForm
from apps.research.models import ResearchJob
from apps.research.services.facebook_browser import canonical_post_url, comment_record, EXPAND, TARGET
from apps.research.facebook_tasks import save_scan_result


class BrowserParsingTests(SimpleTestCase):
    def test_source_links_are_restricted_and_canonical(self):
        self.assertEqual(canonical_post_url('https://www.facebook.com/reel/123/?locale=ru_RU'), 'https://www.facebook.com/reel/123/')
        for url in ['http://127.0.0.1/posts/a', 'https://facebook.com.evil.test/posts/a', 'https://www.facebook.com@evil.test/posts/a', 'https://www.facebook.com/search/posts/?q=test', 'https://www.facebook.com/profile.php?id=2']:
            self.assertEqual(canonical_post_url(url), '')

    def test_comment_ids_and_replies_belong_to_exact_source(self):
        source = 'https://www.facebook.com/reel/123/'
        row = {'texts': ['Boycott PCL', 'Boycott PCL'], 'links': ['https://www.facebook.com/name?comment_id=9', source+'?comment_id=8&reply_comment_id=9&tracking=1']}
        record = comment_record(row, source)
        self.assertEqual(record['id'], '9')
        self.assertEqual(record['parent_id'], '8')
        self.assertEqual(record['text'], 'Boycott PCL')
        self.assertTrue(record['mentions_pcl'])
        self.assertIsNone(comment_record(row, 'https://www.facebook.com/reel/456/'))

    def test_expand_controls_never_match_writing_or_reaction_buttons(self):
        for text in ['Reply', 'Ответить', 'Нравится', 'Опубликовать комментарий', 'Поделиться', 'Follow']:
            self.assertIsNone(EXPAND.search(text))
        for text in ['Показать ещё 4 комментария', 'Посмотреть 1 ответ', 'View 3 replies', 'View more comments']:
            self.assertIsNotNone(EXPAND.search(text))
        self.assertIsNone(TARGET.search('apclz'))

    def test_query_form_rejects_external_urls_and_excessive_queries(self):
        for query in ['https://example.com/secret', '\n'.join(str(i) for i in range(13))]:
            self.assertFalse(FacebookScanForm(data={'query': query, 'depth': 'standard'}).is_valid())


class BrowserPortalTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('operator', is_staff=True)
        self.client.force_login(self.user)

    @patch('apps.research.views.run_facebook_scan.delay', return_value=SimpleNamespace(id='browser-task'))
    @patch('apps.research.views.run_research_job.delay')
    def test_create_and_retry_use_browser_not_remote_engine(self, remote, browser):
        response = self.client.post(reverse('research:facebook_scan_create'), {'query': 'PCL Oilers\nPCL Formenton', 'depth': 'standard'}, secure=True)
        self.assertEqual(response.status_code, 302)
        job = ResearchJob.objects.get()
        self.assertEqual(job.plan['engine'], 'facebook_browser')
        job.mark_finished('completed')
        self.client.post(reverse('research:job_retry', args=[job.pk]), secure=True)
        job.refresh_from_db()
        self.assertEqual(job.plan['engine'], 'facebook_browser')
        self.assertEqual(browser.call_count, 2)
        remote.assert_not_called()

    def test_only_staff_can_start_browser_collection(self):
        self.user.is_staff = False
        self.user.save()
        self.assertEqual(self.client.get(reverse('research:facebook_scan_create'), secure=True).status_code, 403)

    def test_report_keeps_gaps_and_does_not_call_mentions_hostility(self):
        job = ResearchJob.objects.create(query='PCL Oilers')
        result = {'sources': [{'url': 'https://www.facebook.com/reel/1/', 'public_verified': True, 'text': 'PCL built this arena', 'mentions_pcl': True, 'coverage': 'comment_limit_reached', 'sort': 'newest', 'comments': [{'id':'2','url':'https://www.facebook.com/reel/1/?comment_id=2','text':'PCL did good work','mentions_pcl':True}]}], 'queries': [{'query':'PCL Oilers','status':'ok','discovered':4}], 'discovered_urls':['a','b','c','d'], 'unvisited_count':3, 'observed_at':'2026-09-24', 'limitations':'Bounded sample'}
        save_scan_result(job,result)
        self.assertIn('not a finding of hostility', job.report_markdown)
        self.assertIn('comment_limit_reached',job.report_markdown)
        self.assertEqual(job.sources.count(),1)
