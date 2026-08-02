import time
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, SimpleTestCase, TransactionTestCase
from django.urls import reverse
from playwright.sync_api import sync_playwright

from apps.research.forms import FacebookDiscoverySourceForm, FacebookScanForm
from apps.research.models import FacebookDiscoverySource, FacebookDiscoveryRun, ResearchJob
from apps.research.services.facebook_targets import canonical_source_url, public_group_header
