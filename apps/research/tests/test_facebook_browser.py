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
