"""Independent search and source-feed discovery with durable provenance."""
import re
import time
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

from django.utils import timezone
from playwright.sync_api import sync_playwright

from apps.monitoring.services.facebook_auth import facebook_context_kwargs
from apps.monitoring.services.sync_db import call_sync_db
from apps.research.models import FacebookDiscoveryRun, ResearchEvent
