import asyncio
import hashlib
import logging
import os
import random
import re
import shutil
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from django.conf import settings
from django.db import close_old_connections
from django.utils.dateparse import parse_datetime
from playwright.sync_api import Page

from apps.monitoring.models import MonitoredAccount
from apps.monitoring.services.facebook_auth import (
    FacebookAuthError,
    authenticated_session_required_message,
    facebook_auth_enabled,
    facebook_base_context_kwargs,
    new_facebook_context,
)
from apps.monitoring.services.text import normalize_text


logger = logging.getLogger(__name__)

FACEBOOK_BASE_URL = "https://www.facebook.com/"
POST_URL_MARKERS = (
    "/posts/",
    "/permalink/",
    "story_fbid=",
    "/photos/",
    "/videos/",
    "/reel/",
)
TRACKING_QUERY_PARAMS = {
    "__cft__",
    "__tn__",
    "ref",
    "refid",
    "mibextid",
    "paipv",
    "eav",
    "fbclid",
    "locale",
}
STABLE_QUERY_PARAMS = {"story_fbid", "id", "fbid", "set", "type", "v"}
UI_TEXT_LINES = {
    "like",
    "comment",
    "share",
    "send",
    "all reactions:",
    "most relevant",
    "top comments",
    "view more comments",
    "see more",
    "see less",
    "log in",
    "sign up",
}
UNAVAILABLE_MARKERS = (
    "this content isn't available",
    "this content is not available",
    "this content isn't available right now",
    "this content is not available right now",
    "this page isn't available",
    "this page is not available",
    "page isn't available",
    "content not found",
    "this account is private",
    "private account",
    "этот контент сейчас недоступен",
    "контент сейчас недоступен",
    "контент недоступен",
