from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.research.forms import ResearchJobForm, FacebookScanForm, FacebookDiscoverySourceForm
from apps.research.models import ResearchEvent, ResearchJob, ResearchSource, FacebookDiscoverySource
from apps.research.tasks import run_research_job
from apps.research.facebook_tasks import run_facebook_scan


def paginate(request, queryset, per_page=25):
    paginator = Paginator(queryset, per_page)
    return paginator.get_page(request.GET.get("page"))


def visible_jobs(request):
    queryset = ResearchJob.objects.select_related("requested_by")
    if request.user.is_staff:
        return queryset
    return queryset.filter(requested_by=request.user)


def get_visible_job(request, pk):
    return get_object_or_404(visible_jobs(request), pk=pk)


def queue_job(job: ResearchJob) -> None:
    task = run_facebook_scan if job.plan.get("engine") == "facebook_browser" else run_research_job
    async_result = task.delay(job.pk)
    job.task_id = async_result.id or ""
    job.save(update_fields=["task_id", "updated_at"])
