from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.monitoring.forms import PlatformCredentialForm
from apps.monitoring.models import (
    CheckRun,
    Keyword,
    MonitoredAccount,
    Post,
    PostKeywordMatch,
    TelegramMessageTemplate,
)
from apps.monitoring.models import PlatformCredential
from apps.monitoring.services.checker import run_account_check, store_post_candidates
from apps.monitoring.services.facebook_auth import (
    FacebookAuthError,
    facebook_auth_enabled,
    facebook_context_kwargs,
    get_facebook_login_credentials,
)
from apps.monitoring.services.facebook_checker import (
    FacebookPostCandidate,
    FacebookBlocked,
    detect_facebook_block_state,
    external_post_id_for,
    extract_candidates_from_html,
    facebook_post_url_matches_account,
    facebook_route_urls,
    is_low_information_candidate_text,
    is_post_url,
    is_unavailable_only_candidate_text,
    normalize_facebook_url,
)
from apps.monitoring.services.telegram_templates import render_telegram_template


FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"


class FacebookCheckerTests(TestCase):
    def setUp(self):
        self.account = MonitoredAccount.objects.create(
            platform=MonitoredAccount.Platform.FACEBOOK,
            account_url="https://www.facebook.com/SamplePage",
            account_name="Sample Page",
