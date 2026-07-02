import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TransactionTestCase, TestCase, override_settings

from apps.monitoring.management.commands.run_facebook_session_manager import Command
from apps.monitoring.models import TelegramMessageTemplate
from apps.monitoring.services.facebook_auth import facebook_session_state, save_facebook_storage_state
from apps.monitoring.services.facebook_session_requests import build_operator_url
from apps.monitoring.services.telegram_templates import render_telegram_template


class SessionDatabaseTests(TransactionTestCase):
    def test_preflight_and_template_work_inside_running_event_loop(self):
        TelegramMessageTemplate.objects.update_or_create(key='system_alert', defaults={'body': 'Custom {title}'})

        async def run():
            Command()._validate_monitored_accounts(Mock(), SimpleNamespace(pk=1), [])
            self.assertEqual(render_telegram_template('system_alert', context={'title': 'login'}), 'Custom login')

        asyncio.run(run())


class SessionStateTests(SimpleTestCase):
    def page(self, text, url='https://www.facebook.com/me', cookies=True):
        page = Mock(url=url)
        page.locator.return_value.inner_text.return_value = text
        page.context.cookies.return_value = ([{'name': 'c_user', 'value': 'test'}, {'name': 'xs', 'value': 'test'}] if cookies else [])
        return page

    def test_requires_facebook_content_and_authentication_cookies(self):
        self.assertEqual(facebook_session_state(self.page('')), 'unknown')
        self.assertEqual(facebook_session_state(self.page('Some public content', cookies=False)), 'unknown')
        self.assertEqual(facebook_session_state(self.page('Content', url='https://example.com')), 'unknown')
        self.assertEqual(facebook_session_state(self.page('News feed')), 'authenticated')
        self.assertEqual(facebook_session_state(self.page('Enter your authentication code')), 'checkpoint')
        self.assertEqual(facebook_session_state(self.page('Log in to Facebook')), 'login_required')

    def test_atomic_save_preserves_previous_session_on_failure(self):
        with TemporaryDirectory() as root:
            target = Path(root) / 'state.json'
            target.write_text('{"old":true}')
            with override_settings(FACEBOOK_AUTH_STORAGE_STATE_PATH=target):
                context = Mock()
                context.storage_state.side_effect = RuntimeError('interrupted')
                with self.assertRaises(RuntimeError):
                    save_facebook_storage_state(context)
                self.assertEqual(json.loads(target.read_text()), {'old': True})
                context.storage_state.side_effect = lambda path: Path(path).write_text('{"new":true}')
                save_facebook_storage_state(context)
                self.assertEqual(json.loads(target.read_text()), {'new': True})
                self.assertEqual(target.stat().st_mode & 0o777, 0o600)
                self.assertEqual(list(Path(root).iterdir()), [target])

    @override_settings(FACEBOOK_SESSION_MANAGER_URL='https://example.com/facebook-session/vnc.html?path=facebook-session/')
    def test_operator_url_preserves_proxy_path_and_autoconnects(self):
        from urllib.parse import parse_qs, urlsplit
        query = parse_qs(urlsplit(build_operator_url(SimpleNamespace(pk=8), 'test-token')).query)
        self.assertEqual(query['path'], ['facebook-session/'])
        self.assertEqual(query['autoconnect'], ['true'])
        self.assertEqual(query['request'], ['8'])


class SessionAccessTests(TestCase):
    def test_remote_browser_requires_staff_login(self):
        url = '/settings/facebook-login/session-access/'
        self.assertEqual(self.client.get(url, secure=True).status_code, 401)
        user = get_user_model().objects.create_user('operator')
        self.client.force_login(user)
        self.assertEqual(self.client.get(url, secure=True).status_code, 403)
        user.is_staff = True
        user.save()
        self.assertEqual(self.client.get(url, secure=True).status_code, 204)
