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


def operator_view(view):
    @login_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_staff:
            return HttpResponseForbidden('Operator account required.')
        return view(request, *args, **kwargs)
    return wrapper


def monitor_context():
    monitor = FacebookMonitor.objects.filter(pk=1).first()
    active = monitor.jobs.filter(status__in=ACTIVE).first() if monitor else None
    last_post = FacebookPost.objects.filter(public_verified=True).order_by('-last_seen_at').first()
    return {'monitor': monitor, 'active_cycle': active, 'last_observed_at': last_post.last_seen_at if last_post else None,
            'posts_read': FacebookPost.objects.filter(public_verified=True).exclude(text='').count(),
            'posts_found': FacebookPost.objects.count(),
            'comments_read': FacebookComment.objects.count(),
            'active_sources': FacebookDiscoverySource.objects.filter(enabled=True).count()}


@operator_view
def workspace(request):
    # Preserve previously bookmarked POST controls without showing settings in the inbox.
