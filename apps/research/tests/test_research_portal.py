from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.research.models import ResearchJob


class ResearchPortalTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="analyst",
            password="testpass123",
        )
        self.client.force_login(self.user)

    @patch("apps.research.views.run_research_job.delay")
    def test_create_research_job_queues_celery_task(self, delay):
        delay.return_value = SimpleNamespace(id="celery-task-id")

        response = self.client.post(
            reverse("research:job_create"),
            {
                "title": "Acme risk review",
                "query": "Research Acme Corp operational risk.",
                "depth": ResearchJob.Depth.STANDARD,
                "include_social": "on",
            },
        )

        job = ResearchJob.objects.get()
        self.assertRedirects(
            response,
            reverse("research:job_detail", args=[job.pk]),
            fetch_redirect_response=False,
        )
        self.assertEqual(job.requested_by, self.user)
        self.assertEqual(job.task_id, "celery-task-id")
        self.assertEqual(job.status, ResearchJob.Status.QUEUED)
        delay.assert_called_once_with(job.pk)

    def test_status_endpoint_is_login_protected_job_state(self):
        job = ResearchJob.objects.create(
            requested_by=self.user,
            title="Market scan",
            query="Scan market changes.",
            status=ResearchJob.Status.PLANNING,
        )

        response = self.client.get(reverse("research:job_status", args=[job.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], ResearchJob.Status.PLANNING)
