"""Focused, staff-only evidence workspace and reading controls."""
from datetime import timedelta
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.http import HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.research.forms import FacebookMonitorForm
from apps.research.models import (
    FacebookComment, FacebookDiscoverySource, FacebookMonitor, FacebookPost, FacebookReview,
)
from apps.research.services.facebook_monitor import ACTIVE, configure_monitor
from apps.research.services.facebook_workspace import decorate_posts, evidence_posts, pcl_filter


