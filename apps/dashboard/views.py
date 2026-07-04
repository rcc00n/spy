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
