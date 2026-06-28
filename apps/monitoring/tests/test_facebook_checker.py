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
