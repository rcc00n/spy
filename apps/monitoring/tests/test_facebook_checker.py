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
        )
        self.assertEqual(posts_found, 2)
        self.assertEqual(new_posts, 2)
        self.assertEqual(matches, 2)
        self.assertEqual(len(alert_ids), 2)

    def test_duplicate_candidate_does_not_create_duplicate_post(self):
        candidate = FacebookPostCandidate(
            external_post_id="facebook-url:test-id",
            post_url="https://www.facebook.com/SamplePage/posts/111",
            text="Public budget keyword post",
            published_at=None,
            raw_snapshot="raw",
            source_type="post_container",
        )
        store_post_candidates(self.account, [candidate, candidate])
        store_post_candidates(self.account, [candidate])
        self.assertEqual(Post.objects.count(), 1)

    def test_login_wall_candidate_is_not_stored_as_post(self):
        candidate = FacebookPostCandidate(
            external_post_id="facebook-text:login-wall",
            post_url="https://www.facebook.com/login/?next=https%3A%2F%2Fexample.com",
            text=self.fixture("facebook_login_wall_sample.html"),
            published_at=None,
            raw_snapshot="raw",
            source_type="fallback_snapshot",
        )

        posts_found, new_posts, matches, alert_ids = store_post_candidates(
            self.account,
            [candidate],
        )

        self.assertEqual(posts_found, 0)
        self.assertEqual(new_posts, 0)
        self.assertEqual(matches, 0)
        self.assertEqual(alert_ids, [])
        self.assertEqual(Post.objects.count(), 0)

    def test_facebook_candidate_without_post_url_is_not_stored_as_post(self):
        candidate = FacebookPostCandidate(
            external_post_id="facebook-text:no-post-url",
            post_url="https://www.facebook.com/SamplePage",
            text="A long page snapshot that is not tied to a real Facebook post URL.",
            published_at=None,
            raw_snapshot="raw",
            source_type="fallback_snapshot",
        )

        run = CheckRun.objects.create(monitored_account=self.account)
        posts_found, new_posts, matches, alert_ids = store_post_candidates(
            self.account,
            [candidate],
            run=run,
        )

        self.assertEqual(posts_found, 0)
        self.assertEqual(new_posts, 0)
        self.assertEqual(matches, 0)
        self.assertEqual(alert_ids, [])
        self.assertEqual(Post.objects.count(), 0)
        observed = run.observed_posts.get()
        self.assertEqual(observed.status, "skipped")
        self.assertEqual(observed.skip_reason, "missing_post_url")

    def test_facebook_unavailable_only_candidate_is_not_stored_as_post(self):
        candidate = FacebookPostCandidate(
            external_post_id="facebook-url:unavailable-only",
            post_url="https://www.facebook.com/SamplePage/posts/222",
            text=(
                "Sample Page обновил(-а) свой статус. 23 апрель · "
                "Этот контент сейчас недоступен Возможно, владелец удалил "
                "контент или ограничил доступ к нему. Напишите комментарий…"
            ),
            published_at=None,
            raw_snapshot="raw",
            source_type="post_container",
        )

        run = CheckRun.objects.create(monitored_account=self.account)
        posts_found, new_posts, matches, alert_ids = store_post_candidates(
            self.account,
            [candidate],
            run=run,
        )

        self.assertEqual(posts_found, 0)
        self.assertEqual(new_posts, 0)
        self.assertEqual(matches, 0)
        self.assertEqual(alert_ids, [])
        self.assertEqual(Post.objects.count(), 0)
        observed = run.observed_posts.get()
        self.assertEqual(observed.status, "skipped")
        self.assertEqual(observed.skip_reason, "unavailable_only")

    def test_facebook_low_information_candidate_is_not_stored_as_post(self):
        candidate = FacebookPostCandidate(
            external_post_id="facebook-url:low-information",
            post_url="https://www.facebook.com/SamplePage/posts/333",
            text="24 июнь 2025 г.",
            published_at=None,
            raw_snapshot="raw",
            source_type="permalink_link",
        )

        run = CheckRun.objects.create(monitored_account=self.account)
        posts_found, new_posts, matches, alert_ids = store_post_candidates(
            self.account,
            [candidate],
            run=run,
        )

        self.assertEqual(posts_found, 0)
        self.assertEqual(new_posts, 0)
        self.assertEqual(matches, 0)
        self.assertEqual(alert_ids, [])
        self.assertEqual(Post.objects.count(), 0)
        observed = run.observed_posts.get()
        self.assertEqual(observed.status, "skipped")
        self.assertEqual(observed.skip_reason, "low_information_text")

    def test_login_required_check_gets_auth_required_status(self):
        with patch(
            "apps.monitoring.services.checker.fetch_account_candidates",
            side_effect=FacebookBlocked("login_required", "Refresh Facebook session."),
        ):
            run = run_account_check(self.account, send_telegram=False)

        self.account.refresh_from_db()
        self.assertEqual(run.status, CheckRun.Status.AUTH_REQUIRED)
        self.assertEqual(self.account.last_status, CheckRun.Status.AUTH_REQUIRED)

    def test_duplicate_keyword_match_does_not_resend_alert(self):
        Keyword.objects.create(phrase="budget")
        candidate = FacebookPostCandidate(
            external_post_id="facebook-url:test-alert-id",
            post_url="https://www.facebook.com/SamplePage/posts/999",
            text="Public budget keyword post",
            published_at=None,
            raw_snapshot="raw",
            source_type="post_container",
        )

        with patch(
            "apps.monitoring.services.checker.fetch_account_candidates",
            return_value=[candidate],
        ), patch("apps.monitoring.services.checker.send_match_alert") as send_alert:
            run_account_check(self.account)
            run_account_check(self.account)

        self.assertEqual(Post.objects.count(), 1)
        self.assertEqual(PostKeywordMatch.objects.count(), 1)
        self.assertEqual(send_alert.call_count, 1)

    @override_settings(
        FACEBOOK_AUTH_ENABLED=False,
        FACEBOOK_LOGIN_EMAIL="",
        FACEBOOK_LOGIN_PASSWORD="",
    )
    def test_facebook_context_without_auth_has_no_storage_state(self):
        with TemporaryDirectory() as tempdir:
            with override_settings(
                FACEBOOK_AUTH_STORAGE_STATE_PATH=f"{tempdir}/missing-state.json"
            ):
                kwargs = facebook_context_kwargs()

        self.assertNotIn("storage_state", kwargs)
        self.assertIn("user_agent", kwargs)

    @override_settings(
        FACEBOOK_AUTH_ENABLED=False,
        FACEBOOK_LOGIN_EMAIL="",
        FACEBOOK_LOGIN_PASSWORD="",
    )
    def test_admin_credential_enables_facebook_auth(self):
        credential = PlatformCredential(
            platform=PlatformCredential.Platform.FACEBOOK,
            username="operator@example.com",
            is_active=True,
        )
        credential.set_password("secret-password")
        credential.save()

        with TemporaryDirectory() as tempdir:
            with override_settings(
                FACEBOOK_AUTH_STORAGE_STATE_PATH=f"{tempdir}/missing-state.json"
            ):
                self.assertTrue(facebook_auth_enabled())

    @override_settings(
        FACEBOOK_AUTH_ENABLED=True,
        FACEBOOK_AUTH_STORAGE_STATE_PATH="/tmp/spy-missing-facebook-state.json",
    )
    def test_facebook_context_auth_requires_storage_state_file(self):
        with self.assertRaises(FacebookAuthError):
            facebook_context_kwargs()

    @override_settings(FACEBOOK_AUTH_ENABLED=True)
    def test_facebook_context_auth_uses_storage_state_file(self):
        with NamedTemporaryFile() as state_file:
            with override_settings(FACEBOOK_AUTH_STORAGE_STATE_PATH=state_file.name):
                kwargs = facebook_context_kwargs()

        self.assertEqual(kwargs["storage_state"], state_file.name)

    def test_platform_credential_encrypts_password(self):
        credential = PlatformCredential(
            platform=PlatformCredential.Platform.FACEBOOK,
            username="operator@example.com",
            is_active=True,
        )
        credential.set_password("secret-password")
        credential.save()

        self.assertNotIn("secret-password", credential.encrypted_password)
        self.assertEqual(credential.get_password(), "secret-password")

    def test_platform_credential_form_preserves_password_when_blank(self):
        credential = PlatformCredential(
            platform=PlatformCredential.Platform.FACEBOOK,
            username="operator@example.com",
            is_active=True,
        )
        credential.set_password("secret-password")
        credential.save()

        form = PlatformCredentialForm(
            data={
                "platform": PlatformCredential.Platform.FACEBOOK,
                "username": "new-operator@example.com",
                "password": "",
                "is_active": "on",
            },
            instance=credential,
        )

        self.assertTrue(form.is_valid(), form.errors)
        updated = form.save()
        self.assertEqual(updated.username, "new-operator@example.com")
        self.assertEqual(updated.get_password(), "secret-password")

    @override_settings(FACEBOOK_LOGIN_EMAIL="", FACEBOOK_LOGIN_PASSWORD="")
    def test_facebook_credentials_fall_back_to_admin_credential(self):
        credential = PlatformCredential(
            platform=PlatformCredential.Platform.FACEBOOK,
            username="operator@example.com",
            is_active=True,
        )
        credential.set_password("secret-password")
        credential.save()

        username, password, stored_credential = get_facebook_login_credentials()

        self.assertEqual(username, "operator@example.com")
        self.assertEqual(password, "secret-password")
        self.assertEqual(stored_credential.pk, credential.pk)

    def test_telegram_template_renders_placeholders(self):
        TelegramMessageTemplate.objects.create(
            key="test_template",
            name="Test template",
            body="Account {account_name}: {status}. Unknown {missing}",
        )

        rendered = render_telegram_template(
            "test_template",
            context={"account_name": "Sample Page", "status": "active"},
            default="fallback",
        )

        self.assertEqual(rendered, "Account Sample Page: active. Unknown {missing}")
