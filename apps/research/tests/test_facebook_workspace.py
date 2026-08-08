from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from apps.research.models import FacebookComment, FacebookPost, FacebookReview, FacebookDiscoverySource, ResearchJob
from apps.research.services.facebook_store import enqueue_url, persist_snapshot


class WorkspaceTests(TestCase):
    def setUp(self):
