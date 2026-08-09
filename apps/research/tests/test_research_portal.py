from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.research.models import ResearchJob


class ResearchPortalTests(TestCase):
    def setUp(self):
