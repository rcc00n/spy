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
