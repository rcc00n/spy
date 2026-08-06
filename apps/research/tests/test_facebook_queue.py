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
