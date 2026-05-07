from datetime import timedelta
from pathlib import Path

import dj_database_url
import environ
from django.core.exceptions import ImproperlyConfigured
from django.core.management.utils import get_random_secret_key


BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
