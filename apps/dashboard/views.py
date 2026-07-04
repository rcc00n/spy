from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib.auth.decorators import user_passes_test

from .forms import (
    KeywordForm,
    ManualCheckForm,
    MonitoredAccountForm,
    PlatformCredentialForm,
)
from apps.monitoring.models import (
    CheckRun,
    CheckRunPost,
    FacebookSessionRefreshRequest,
    Keyword,
    ManualCheckJob,
    MonitoredAccount,
    PlatformCredential,
    PostKeywordMatch,
)
from apps.monitoring.tasks import run_manual_check_job
from apps.monitoring.services.facebook_auth import (
    facebook_auth_enabled,
    storage_state_path,
)
from apps.monitoring.services.facebook_session_requests import (
    create_facebook_session_request,
)
from apps.monitoring.services.runner import (
    accounts_for_check,
)


def paginate(request, queryset, per_page=25):
    paginator = Paginator(queryset, per_page)
    return paginator.get_page(request.GET.get("page"))


def staff_required(view_func):
    return user_passes_test(lambda user: user.is_staff)(view_func)


def facebook_session_access(request):
    """Authorize the operator's noVNC HTTP and WebSocket routes."""
    if not request.user.is_authenticated:
        return HttpResponse(status=401)
    return HttpResponse(status=204 if request.user.is_staff else 403)


def facebook_credential_status():
    credential = PlatformCredential.objects.filter(
        platform=PlatformCredential.Platform.FACEBOOK,
        is_active=True,
    ).first()
    return {
        "facebook_auth_enabled": facebook_auth_enabled(),
        "facebook_auth_setting_enabled": settings.FACEBOOK_AUTH_ENABLED,
        "facebook_credential": credential,
        "facebook_session_exists": storage_state_path().exists(),
        "facebook_storage_state_path": storage_state_path(),
        "facebook_session_manager_url": settings.FACEBOOK_SESSION_MANAGER_URL,
        "recent_session_requests": FacebookSessionRefreshRequest.objects.order_by(
            "-requested_at"
        )[:10],
    }


@login_required
def index(request):
    if request.user.is_staff and request.GET.get("legacy") != "1":
        return redirect("research:facebook_monitor")
    context = {
        "active_accounts": MonitoredAccount.objects.filter(is_active=True).count(),
        "active_keywords": Keyword.objects.filter(is_active=True).count(),
        "total_matches": PostKeywordMatch.objects.count(),
        "recent_matches": PostKeywordMatch.objects.select_related(
            "keyword",
            "post__monitored_account",
        ).order_by("-created_at")[:10],
        "recent_runs": CheckRun.objects.select_related("monitored_account").order_by(
            "-started_at"
        )[:10],
        "recent_manual_jobs": ManualCheckJob.objects.select_related(
            "account",
            "requested_by",
        ).order_by("-requested_at")[:10],
        "error_runs": CheckRun.objects.filter(status=CheckRun.Status.ERROR).count(),
        "accounts_status": MonitoredAccount.objects.annotate(
            match_count=Count("posts__keyword_matches")
        ).order_by("platform", "account_name"),
        "manual_check_form": ManualCheckForm(),
    }
    context.update(facebook_credential_status())
    return render(request, "dashboard/index.html", context)


@login_required
def run_check_now(request):
    if request.method != "POST":
        return redirect("dashboard:index")

    form = ManualCheckForm(request.POST)
    if not form.is_valid():
