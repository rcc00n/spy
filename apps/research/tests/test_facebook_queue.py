from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.research.models import (FacebookComment, FacebookCommentRevision, FacebookPost,
                                  FacebookThreadWork, ResearchJob, ResearchSource)
from apps.research.facebook_tasks import scan_step, run_facebook_scan, dispatch_facebook_scans
from apps.research.services.facebook_browser import FacebookAccessStopped
from apps.research.services.facebook_store import (enqueue_url, persist_snapshot, finish_attempt,
                                                  import_legacy_job, update_report)

URL = 'https://www.facebook.com/reel/123/'


def snapshot(text='PCL built this', comment_text='Good work PCL', coverage='collecting'):
    return {'url': URL, 'text': text, 'public_verified': True, 'mentions_pcl': True,
            'sort': 'all', 'coverage': coverage, 'comments': [
                {'id': '42', 'parent_id': '40', 'url': URL + '?comment_id=40&reply_comment_id=42',
                 'text': comment_text, 'mentions_pcl': 'PCL' in comment_text}]}


class CorpusTests(TestCase):
    def setUp(self):
        self.job = ResearchJob.objects.create(query=URL, plan={'engine': 'facebook_browser'})
        self.work = enqueue_url(self.job, URL, ['PCL'])

    def test_checkpoint_is_idempotent_across_jobs_and_records_observed_revisions(self):
        persist_snapshot(self.work.pk, snapshot())
        persist_snapshot(self.work.pk, snapshot())
        second = ResearchJob.objects.create(query=URL)
        second_work = enqueue_url(second, URL+'?locale=ru_RU')
        persist_snapshot(second_work.pk, snapshot())
        self.assertEqual(FacebookPost.objects.count(), 1)
        self.assertEqual(FacebookComment.objects.count(), 1)
        self.assertEqual(FacebookCommentRevision.objects.count(), 1)
        persist_snapshot(second_work.pk, snapshot(comment_text='Changed PCL text'))
        self.assertEqual(FacebookCommentRevision.objects.count(), 2)
        self.assertEqual(self.work.comments.get().text, 'Changed PCL text')
        self.assertEqual(self.work.comments.get().parent_id, '40')

    def test_failure_and_missing_visibility_do_not_erase_checkpoint(self):
        persist_snapshot(self.work.pk, snapshot())
        persist_snapshot(self.work.pk, {'url': URL, 'public_verified': False, 'comments': [], 'coverage': 'error'})
        finish_attempt(self.work, error='Browser crashed')
        self.assertEqual(FacebookComment.objects.get().text, 'Good work PCL')
        self.assertEqual(self.job.sources.get().payload['comments'][0]['id'], '42')
        self.assertEqual(self.job.sources.get().payload['coverage'], 'error')

    def test_private_or_other_post_evidence_is_not_saved(self):
        private = snapshot()
        private['public_verified'] = False
        persist_snapshot(self.work.pk, private)
        self.assertEqual(FacebookComment.objects.count(), 0)
        wrong = snapshot()
        wrong['url'] = 'https://www.facebook.com/reel/456/'
        with self.assertRaises(ValueError):
            persist_snapshot(self.work.pk, wrong)
        foreign_comment = snapshot()
        foreign_comment['comments'][0]['url'] = 'https://www.facebook.com/reel/456/?comment_id=42'
        persist_snapshot(self.work.pk, foreign_comment)
        self.assertEqual(FacebookComment.objects.count(), 0)

    def test_legacy_import_is_idempotent_and_leaves_job_terminal(self):
        self.work.delete()
        self.job.plan = {'engine': 'facebook_browser', 'discovered_urls': [URL],
                         'queries': [{'query': URL, 'status': 'ok'}]}
        self.job.status = 'completed'
        self.job.save()
        ResearchSource.objects.create(job=self.job, url=URL, payload=snapshot(coverage='comment_limit_reached'))
        import_legacy_job(self.job)
        import_legacy_job(self.job)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, 'completed')
        self.assertEqual(self.job.facebook_threads.get().status, 'partial')
        self.assertEqual(FacebookComment.objects.count(), 1)
        self.assertEqual(FacebookCommentRevision.objects.count(), 1)

    def test_limit_retries_then_remains_explicit_partial_gap(self):
        for attempt, expected in [(1, 'queued'), (2, 'queued'), (3, 'partial')]:
