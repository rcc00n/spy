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
