from datetime import timedelta
import time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, SimpleTestCase, Client
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import sync_playwright

from apps.research.facebook_tasks import dispatch_facebook_scans, run_facebook_scan, scan_step
from apps.research.models import FacebookMonitor, FacebookDiscoverySource, ResearchJob
from apps.research.services.facebook_browser import FacebookAccessStopped, collect_thread
from apps.research.services.facebook_monitor import configure_monitor, schedule_cycle
from apps.research.services.facebook_store import enqueue_url, persist_snapshot
from apps.research.tests.test_facebook_queue import URL, snapshot


class ScheduleTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.monitor = FacebookMonitor.objects.create(pk=1, enabled=True)
        self.source = FacebookDiscoverySource.objects.create(name='PCL', kind='search', target='PCL Oilers')

    def due_only(self, lane):
        for key in ('discovery', 'refresh', 'backfill'):
            setattr(self.monitor, key + '_due_at', self.now if key == lane else self.now + timedelta(days=1))
        self.monitor.save()

    def known_post(self, url=URL, status='partial'):
        old = ResearchJob.objects.create(query=url, status='failed', depth='standard')
        work = enqueue_url(old, url)
        data = snapshot(); data['url'] = url
        data['comments'][0]['url'] = url + '?comment_id=42'
        persist_snapshot(work.pk, data)
        work.refresh_from_db(); work.status = status; work.attempts = 3; work.save()
        return work

    def test_repeated_ticks_create_one_cycle_and_source_settings_are_snapshotted(self):
        self.due_only('discovery')
        FacebookDiscoverySource.objects.create(name='Disabled', kind='search', target='Disabled', enabled=False)
        job = schedule_cycle(self.now)
        self.assertIsNone(schedule_cycle(self.now))
        self.assertIsNone(schedule_cycle(self.now + timedelta(days=3)))
        self.source.target = 'Changed'; self.source.save()
        self.assertEqual(job.facebook_discoveries.get().target, 'PCL Oilers')
        self.assertEqual(job.monitor_lane, 'discovery')
        self.monitor.refresh_from_db()
        self.assertEqual(self.monitor.discovery_due_at, self.now + timedelta(hours=6))

    def test_completed_cycles_do_not_catch_up_every_missed_interval(self):
        self.due_only('discovery')
        job = schedule_cycle(self.now); job.mark_finished('completed')
        later = self.now + timedelta(days=30)
        next_job = schedule_cycle(later)
        next_job.mark_finished('completed')
        self.assertIsNone(schedule_cycle(later))
        self.monitor.refresh_from_db()
        self.assertEqual(self.monitor.discovery_due_at, later + timedelta(hours=6))

    def test_rotation_checks_unvisited_threads_before_rechecking_previous_batch(self):
        first = self.known_post(); second = self.known_post('https://www.facebook.com/reel/456/')
        self.monitor.refresh_batch = 1; self.due_only('refresh')
        job = schedule_cycle(self.now)
        self.assertEqual(job.facebook_threads.get().post_id, first.post_id)
        job.mark_finished('completed')
        self.monitor.refresh_from_db(); self.due_only('refresh')
        next_job = schedule_cycle(self.now)
        self.assertEqual(next_job.facebook_threads.get().post_id, second.post_id)
        self.assertEqual(first.comments.count(), 1)

    def test_backfill_retains_known_comments_and_depth_but_skips_sampled_threads(self):
        old = self.known_post(); self.known_post('https://www.facebook.com/reel/456/', status='sampled')
        self.due_only('backfill')
        job = schedule_cycle(self.now)
        work = job.facebook_threads.get()
        self.assertEqual(work.post_id, old.post_id)
        self.assertEqual(work.comments.get().pk, old.comments.get().pk)
        self.assertEqual(work.attempts, 3)
        self.assertEqual(work.cycle_attempts, 0)
        self.assertEqual(work.checkpoint['seeded_comments'], 1)
        self.assertEqual(job.depth, 'deep')

    def test_caption_and_refresh_passes_do_not_reset_deep_completion(self):
