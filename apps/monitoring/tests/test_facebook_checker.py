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
            max_posts_per_check=5,
            scroll_rounds=1,
        )

    def fixture(self, name):
        return (FIXTURES / name).read_text()

    def test_blocked_state_detection(self):
        self.assertEqual(
            detect_facebook_block_state(self.fixture("facebook_login_wall_sample.html")),
            "login_required",
        )
        self.assertEqual(
            detect_facebook_block_state(self.fixture("facebook_unavailable_sample.html")),
            "private_or_unavailable",
        )
        self.assertEqual(
            detect_facebook_block_state(self.fixture("facebook_public_page_sample.html")),
            "ok",
        )
        self.assertEqual(
            detect_facebook_block_state(
                "Enter your authentication code",
                "https://www.facebook.com/two_step_verification/authentication/",
            ),
            "captcha_or_checkpoint",
        )
        self.assertEqual(
            detect_facebook_block_state("Этот контент сейчас недоступен"),
            "private_or_unavailable",
        )
        self.assertEqual(
            detect_facebook_block_state(
                "Gurtej Singh 14 ноябрь 2025 г. Albertans are ready for change. "
                "Этот контент сейчас недоступен",
                has_post_evidence=True,
            ),
            "ok",
        )
        self.assertEqual(
            detect_facebook_block_state(
                "Naheed закрыл(-а) профиль Только друзья этого человека видят, "
                "чем он делится в своем профиле."
            ),
            "private_or_unavailable",
        )

    def test_reel_navigation_url_is_not_a_post_url(self):
        self.assertFalse(is_post_url("https://www.facebook.com/reel/?s=tab"))
        self.assertFalse(is_post_url("https://www.facebook.com/reel"))
        self.assertTrue(is_post_url("https://www.facebook.com/reel/230031623078487"))

    def test_unavailable_only_candidate_text_detection(self):
        self.assertTrue(
            is_unavailable_only_candidate_text(
                "Gurtej Singh Brar обновил(-а) свой статус. 23 апрель · "
                "Этот контент сейчас недоступен Возможно, владелец удалил "
                "контент или ограничил доступ к нему. Напишите комментарий…",
                account_name="Gurtej Singh",
            )
        )
        self.assertFalse(
            is_unavailable_only_candidate_text(
                "Gurtej Singh Brar 14 ноябрь 2025 г. · Albertans are ready "
                "for change. Этот контент сейчас недоступен Возможно, "
                "владелец удалил контент или ограничил доступ к нему.",
                account_name="Gurtej Singh",
            )
        )

    def test_low_information_candidate_text_detection(self):
        self.assertTrue(
            is_low_information_candidate_text(
                "24 июнь 2025 г.",
                account_name="Gurtej Singh",
            )
        )
        self.assertFalse(
            is_low_information_candidate_text(
                "The campaign may be over, but the work starts now.",
                account_name="Gurtej Singh",
            )
        )

    def test_post_url_normalization(self):
        normalized = normalize_facebook_url(
            "https://facebook.com/permalink.php?ref=feed&story_fbid=222&id=333&fbclid=abc"
        )
        self.assertEqual(
            normalized,
            "https://www.facebook.com/permalink.php?id=333&story_fbid=222",
        )

    def test_profile_php_routes_keep_profile_id(self):
        routes = facebook_route_urls("https://www.facebook.com/profile.php?id=61590506608414")

        self.assertIn(
            "https://www.facebook.com/profile.php?id=61590506608414",
            routes,
        )
        self.assertIn(
            "https://www.facebook.com/profile.php?id=61590506608414&sk=posts",
            routes,
        )
        self.assertNotIn("https://www.facebook.com/profile.php/posts", routes)
        self.assertTrue(all("profile.php/posts" not in route for route in routes))
        self.assertTrue(all("id=61590506608414" in route for route in routes))

    def test_external_post_id_stability(self):
        first = external_post_id_for(
            self.account,
            post_url="https://facebook.com/SamplePage/posts/111?fbclid=abc",
            text="ignored text",
        )
        second = external_post_id_for(
            self.account,
            post_url="https://www.facebook.com/SamplePage/posts/111",
            text="different ignored text",
        )
        self.assertEqual(first, second)

    def test_slug_account_allows_permalink_profile_id_post(self):
        self.assertTrue(
            facebook_post_url_matches_account(
                "https://www.facebook.com/SamplePage",
                "https://www.facebook.com/permalink.php?id=333&story_fbid=222",
            )
        )
        self.assertFalse(
            facebook_post_url_matches_account(
                "https://www.facebook.com/SamplePage",
                "https://www.facebook.com/OtherPage/posts/222",
            )
        )

    def test_parser_returns_multiple_post_candidates(self):
        candidates = extract_candidates_from_html(
            self.fixture("facebook_public_page_sample.html"),
            self.account,
            limit=5,
        )
        self.assertEqual(len(candidates), 2)
        self.assertIn("city council budget", candidates[0].text)
        self.assertIn("story_fbid=222", candidates[1].post_url)

    def test_keyword_matching_on_extracted_candidates(self):
        Keyword.objects.create(phrase="budget")
        Keyword.objects.create(phrase="transit")
        candidates = extract_candidates_from_html(
            self.fixture("facebook_public_page_sample.html"),
            self.account,
            limit=5,
        )
        posts_found, new_posts, matches, alert_ids = store_post_candidates(
            self.account,
            candidates,
