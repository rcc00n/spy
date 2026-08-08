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
            self.work.cycle_attempts = attempt
            self.work.save()
            finish_attempt(self.work, snapshot(coverage='comment_limit_reached'))
            self.work.refresh_from_db()
            self.assertEqual(self.work.status, expected)
        update_report(self.job)
        self.assertIn('partial', self.job.report_markdown)
        self.assertIn('not complete coverage', self.job.report_markdown)

    @patch('apps.research.views.run_facebook_scan.delay', return_value=SimpleNamespace(id='resume-task'))
    def test_continue_keeps_sources_comments_and_discovery_progress(self, delay):
        persist_snapshot(self.work.pk, snapshot())
        finish_attempt(self.work, snapshot(coverage='comment_limit_reached'))
        self.work.status = 'partial'
        self.work.attempts = 3
        self.work.cycle_attempts = 3
        self.work.save()
        self.job.plan.update(collector_version=2, discovery_index=1)
        self.job.status = 'failed'
        self.job.save()
        self.client.force_login(get_user_model().objects.create_user('operator', is_staff=True))
        response = self.client.post(reverse('research:job_retry', args=[self.job.pk]), secure=True)
        self.assertEqual(response.status_code, 302)
        self.job.refresh_from_db()
        self.work.refresh_from_db()
        self.assertEqual(self.job.sources.count(), 1)
        self.assertEqual(self.work.comments.count(), 1)
        self.assertEqual(self.work.attempts, 3)
        self.assertEqual(self.work.cycle_attempts, 0)
        self.assertEqual(self.work.status, 'queued')
        self.assertEqual(self.job.plan['discovery_index'], 1)
        page = self.client.get(reverse('research:job_detail', args=[self.job.pk]), secure=True)
        self.assertContains(page, 'Discussion queue')
        self.assertContains(page, 'Good work PCL')


class DurableTaskTests(TestCase):
    def setUp(self):
        self.job = ResearchJob.objects.create(query=URL, depth='standard', status='social_collecting',
            plan={'engine': 'facebook_browser', 'collector_version': 2, 'discovery_index': 1})
        self.work = enqueue_url(self.job, URL)
        self.playwright = patch('apps.research.facebook_tasks.sync_playwright')
        self.playwright.start()
        self.addCleanup(self.playwright.stop)
        auth = patch('apps.research.facebook_tasks.facebook_context_kwargs', return_value={})
        auth.start()
        self.addCleanup(auth.stop)

    @patch('apps.research.facebook_tasks.collect_thread')
    def test_exception_after_checkpoint_preserves_data_and_retry_deduplicates(self, collect):
        def crash(page, url, limit, deadline, checkpoint, **kwargs):
            checkpoint(snapshot())
            raise RuntimeError('Detached dialog')
        collect.side_effect = crash
        scan_step(self.job)
        self.work.refresh_from_db()
        self.assertEqual(self.work.status, 'queued')
        self.assertEqual(self.work.comments.count(), 1)
        self.assertEqual(self.job.sources.count(), 1)
        self.work.next_attempt_at = timezone.now() - timedelta(seconds=1)
        self.work.save()
        collect.side_effect = None
        collect.return_value = snapshot(coverage='no_new_comments_after_scroll')
        scan_step(self.job)
        self.work.refresh_from_db()
        self.assertEqual(self.work.attempts, 2)
        self.assertEqual(self.work.status, 'sampled')
        self.assertEqual(FacebookComment.objects.count(), 1)
        self.assertEqual(FacebookCommentRevision.objects.count(), 1)
        self.assertEqual(collect.call_args.args[2], 41)  # Existing evidence + new batch.
        self.assertEqual(self.job.status, 'completed')

    @patch('apps.research.facebook_tasks.collect_thread', return_value=snapshot(coverage='no_new_comments_after_scroll'))
    def test_interrupted_running_row_is_reclaimed(self, collect):
        self.work.status = 'running'
        self.work.attempts = 1
        self.work.cycle_attempts = 1
        self.work.save()
        scan_step(self.job)
        self.work.refresh_from_db()
        self.assertEqual(self.work.attempts, 2)
        self.assertEqual(self.work.status, 'sampled')

    @patch('apps.research.facebook_tasks.discover')
    def test_discovered_urls_survive_search_error(self, discover):
        self.job.query = 'PCL Oilers'
        self.job.plan['discovery_index'] = 0
        self.job.save()
        def crash(page, query, limit, rounds, deadline, on_urls):
            on_urls(['https://www.facebook.com/reel/456/'])
            raise RuntimeError('Search interrupted')
        discover.side_effect = crash
        scan_step(self.job)
        self.assertEqual(self.job.facebook_threads.count(), 2)
        self.assertEqual(self.job.plan['queries'][0]['status'], 'error')

    @patch('apps.research.facebook_tasks.Redis')
    @patch('apps.research.facebook_tasks.collect_thread', side_effect=FacebookAccessStopped('captcha_or_checkpoint'))
    def test_access_challenge_pauses_queue_without_erasing_evidence(self, collect, redis):
        redis.from_url.return_value.lock.return_value.acquire.return_value = True
        persist_snapshot(self.work.pk, snapshot())
        another = ResearchJob.objects.create(query=URL, plan={'engine': 'facebook_browser'})
        self.assertEqual(run_facebook_scan.run(self.job.pk), 'access_stopped')
        self.work.refresh_from_db()
        another.refresh_from_db()
        self.assertEqual(self.work.status, 'blocked')
        self.assertEqual(self.work.comments.count(), 1)
        self.assertEqual(another.status, 'failed')

    @patch('apps.research.facebook_tasks.Redis')
    def test_busy_lock_keeps_job_pending(self, redis):
        redis.from_url.return_value.lock.return_value.acquire.return_value = False
        self.assertEqual(run_facebook_scan.run(self.job.pk), 'busy')
        self.work.refresh_from_db()
        self.assertEqual(self.work.attempts, 0)

    @patch('apps.research.facebook_tasks.run_facebook_scan.delay')
    def test_dispatcher_recovers_active_jobs_but_never_restarts_finished_jobs(self, delay):
        ResearchJob.objects.create(query=URL, status='completed', plan={'engine': 'facebook_browser'})
        ResearchJob.objects.create(query='Remote research')
        dispatch_facebook_scans()
        delay.assert_called_once_with(self.job.pk)
