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
        messages.error(request, "Manual check limit must be between 1 and 20.")
        return redirect(request.POST.get("next") or "dashboard:index")

    account_id = request.POST.get("account_id") or None
    accounts = accounts_for_check(force=True, account_id=account_id)
    if not accounts:
        messages.info(request, "No active monitored accounts found for manual check.")
        return redirect(request.POST.get("next") or "dashboard:index")

    job = ManualCheckJob.objects.create(
        requested_by=request.user,
        account=accounts[0] if account_id else None,
        post_limit=form.cleaned_data.get("limit"),
    )
    async_result = run_manual_check_job.delay(job.pk)
    job.task_id = async_result.id or ""
    job.save(update_fields=["task_id"])
    messages.success(
        request,
        (
            f"Manual check queued as job #{job.pk} for {len(accounts)} account(s). "
            "Refresh the dashboard to see status."
        ),
    )
    return redirect(request.POST.get("next") or "dashboard:index")


@login_required
def accounts(request):
    queryset = MonitoredAccount.objects.order_by("platform", "account_name")
    return render(
        request,
        "dashboard/accounts.html",
        {"page_obj": paginate(request, queryset), "manual_check_form": ManualCheckForm()},
    )


@login_required
def account_create(request):
    if request.method == "POST":
        form = MonitoredAccountForm(request.POST)
        if form.is_valid():
            account = form.save()
            messages.success(request, f"Added monitored account {account.account_name}.")
            return redirect("dashboard:accounts")
    else:
        form = MonitoredAccountForm(initial={"is_active": True})

    return render(
        request,
        "dashboard/account_form.html",
        {"form": form, "title": "Add monitored account", "submit_label": "Add account"},
    )


@login_required
def account_edit(request, pk):
    account = get_object_or_404(MonitoredAccount, pk=pk)
    if request.method == "POST":
        form = MonitoredAccountForm(request.POST, instance=account)
        if form.is_valid():
            account = form.save()
            messages.success(request, f"Updated monitored account {account.account_name}.")
            return redirect("dashboard:accounts")
    else:
        form = MonitoredAccountForm(instance=account)

    return render(
        request,
        "dashboard/account_form.html",
        {"form": form, "title": "Edit monitored account", "submit_label": "Save changes"},
    )


@login_required
def account_deactivate(request, pk):
    account = get_object_or_404(MonitoredAccount, pk=pk)
    if request.method == "POST":
        account.is_active = False
        account.save(update_fields=["is_active", "updated_at"])
        messages.success(request, f"Deactivated monitored account {account.account_name}.")
        return redirect("dashboard:accounts")

    return render(
        request,
        "dashboard/confirm_deactivate.html",
        {
            "title": "Deactivate monitored account",
            "object_label": account.account_name,
            "cancel_url": "dashboard:accounts",
        },
    )


@login_required
def keywords(request):
    queryset = Keyword.objects.order_by("phrase")
    return render(
        request,
        "dashboard/keywords.html",
        {"page_obj": paginate(request, queryset)},
    )


@login_required
def keyword_create(request):
    if request.method == "POST":
        form = KeywordForm(request.POST)
        if form.is_valid():
            keyword = form.save()
            messages.success(request, f"Added keyword {keyword.phrase}.")
            return redirect("dashboard:keywords")
    else:
        form = KeywordForm(initial={"is_active": True})

    return render(
        request,
        "dashboard/keyword_form.html",
        {"form": form, "title": "Add keyword", "submit_label": "Add keyword"},
    )


@login_required
def keyword_edit(request, pk):
    keyword = get_object_or_404(Keyword, pk=pk)
    if request.method == "POST":
        form = KeywordForm(request.POST, instance=keyword)
        if form.is_valid():
            keyword = form.save()
            messages.success(request, f"Updated keyword {keyword.phrase}.")
            return redirect("dashboard:keywords")
    else:
        form = KeywordForm(instance=keyword)

    return render(
        request,
        "dashboard/keyword_form.html",
        {"form": form, "title": "Edit keyword", "submit_label": "Save changes"},
    )


@login_required
def keyword_deactivate(request, pk):
    keyword = get_object_or_404(Keyword, pk=pk)
    if request.method == "POST":
        keyword.is_active = False
        keyword.save(update_fields=["is_active"])
        messages.success(request, f"Deactivated keyword {keyword.phrase}.")
        return redirect("dashboard:keywords")

    return render(
        request,
        "dashboard/confirm_deactivate.html",
        {
            "title": "Deactivate keyword",
            "object_label": keyword.phrase,
            "cancel_url": "dashboard:keywords",
        },
    )


@login_required
def matches(request):
    queryset = PostKeywordMatch.objects.select_related(
        "keyword",
        "post",
        "post__monitored_account",
    ).order_by("-created_at")
