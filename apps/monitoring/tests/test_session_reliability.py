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
