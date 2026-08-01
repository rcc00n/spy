from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from apps.research.forms import FacebookScanForm
from apps.research.models import ResearchJob
from apps.research.services.facebook_browser import canonical_post_url, comment_record, EXPAND, TARGET
from apps.research.facebook_tasks import save_scan_result

