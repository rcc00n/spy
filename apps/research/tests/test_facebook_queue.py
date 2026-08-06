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
