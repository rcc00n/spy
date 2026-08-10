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


@login_required
def jobs(request):
    queryset = visible_jobs(request).annotate(source_count=Count("sources"))
    context = {
        "page_obj": paginate(request, queryset),
        "queued_count": queryset.filter(status=ResearchJob.Status.QUEUED).count(),
        "running_count": queryset.exclude(
            status__in=ResearchJob.TERMINAL_STATUSES
        ).count(),
        "completed_count": queryset.filter(status=ResearchJob.Status.COMPLETED).count(),
        "failed_count": queryset.filter(status=ResearchJob.Status.FAILED).count(),
    }
    return render(request, "research/jobs.html", context)


@login_required
def job_create(request):
    if request.method == "POST":
        form = ResearchJobForm(request.POST)
        if form.is_valid():
            job = form.save(commit=False)
            job.requested_by = request.user
            job.status = ResearchJob.Status.QUEUED
            job.save()
            try:
                queue_job(job)
            except Exception as exc:
                job.mark_finished(ResearchJob.Status.FAILED, str(exc)[:4000])
                messages.error(
                    request,
                    f"Research job was saved but could not be queued: {exc}",
                )
            else:
                messages.success(request, f"Queued research job #{job.pk}.")
            return redirect("research:job_detail", pk=job.pk)
    else:
        form = ResearchJobForm(
            initial={
                "include_social": True,
                "depth": ResearchJob.Depth.STANDARD,
            }
        )

    return render(
        request,
        "research/job_form.html",
        {"form": form, "title": "New research job", "submit_label": "Queue research"},
    )


@login_required
def job_detail(request, pk):
    job = get_visible_job(request, pk)
    events = list(job.events.order_by("-created_at")[:50])
    events.reverse()
    sources = job.sources.all()
    return render(
        request,
        "research/job_detail.html",
        {
            "job": job,
            "events": events,
            "sources": sources,
            "facebook_discoveries": job.facebook_discoveries.annotate(post_count=Count("posts")),
            "facebook_threads": job.facebook_threads.select_related("post").annotate(comment_count=Count("comments")).order_by("pk"),
        },
    )


@login_required
def job_status(request, pk):
    job = get_visible_job(request, pk)
    events = list(
        job.events.order_by("-created_at").values("level", "message", "created_at")[:50]
    )
    events.reverse()
    return JsonResponse(
        {
            "id": job.pk,
            "status": job.status,
            "status_display": job.get_status_display(),
            "is_terminal": job.is_terminal,
            "updated_at": timezone.localtime(job.updated_at).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "completed_at": timezone.localtime(job.completed_at).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            if job.completed_at
            else "",
            "error_message": job.error_message,
            "event_count": job.events.count(),
            "source_count": job.sources.count(),
            "report_available": bool(job.report_markdown),
            "events": [
                {
                    "level": event["level"],
                    "message": event["message"],
                    "created_at": timezone.localtime(event["created_at"]).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                }
                for event in events
            ],
        }
    )


@login_required
def job_retry(request, pk):
    if request.method != "POST":
        return redirect("research:job_detail", pk=pk)

    job = get_visible_job(request, pk)
    if job.plan.get("engine") == "facebook_browser" and not request.user.is_staff:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("Facebook collection requires an operator account.")
    if job.monitor_id:
        messages.info(request, 'Scheduled cycles are managed by the Facebook monitor. Use its pause/resume controls; future cycles retain saved evidence.')
        return redirect('research:facebook_monitor')
    if not job.is_terminal:
        messages.info(
            request,
            "Only completed, failed, or cancelled jobs can be queued again.",
        )
        return redirect("research:job_detail", pk=job.pk)

    is_facebook = job.plan.get("engine") == "facebook_browser"
    with transaction.atomic():
        job = ResearchJob.objects.select_for_update().get(pk=job.pk)
        if not job.is_terminal:
            return redirect("research:job_detail", pk=job.pk)
        if is_facebook:
            from apps.research.services.facebook_store import import_legacy_job
            import_legacy_job(job)
            job.facebook_discoveries.exclude(status='done').update(status='queued', cycle_attempts=0, next_attempt_at=timezone.now())
            # Resume gaps first. A finished scan can explicitly recheck its
            # sampled threads, while retaining the corpus and text revisions.
            threads = job.facebook_threads.exclude(status__in=["sampled", "post_only"])
            if not threads.exists() and not job.facebook_discoveries.exclude(status='done').exists():
                threads = job.facebook_threads.all()
            threads.update(status="queued", cycle_attempts=0, next_attempt_at=timezone.now())
            errors = [i for i, q in enumerate(job.plan.get("queries", [])) if q.get("status") == "error"]
            if errors:
                job.plan["discovery_index"] = min(errors)
        else:
            job.report_markdown = ""
            job.plan = {}
            ResearchSource.objects.filter(job=job).delete()
        job.status = ResearchJob.Status.QUEUED
        job.task_id = ""
        job.external_job_id = ""
        job.error_message = ""
        job.completed_at = None
        if not is_facebook:
            job.started_at = None
        job.save()
        ResearchEvent.objects.create(job=job, message="Collection resumed; saved evidence retained." if is_facebook else "Research job queued again.")
    try:
        queue_job(job)
    except Exception as exc:
        job.mark_finished(ResearchJob.Status.FAILED, str(exc)[:4000])
        messages.error(request, f"Research job could not be queued again: {exc}")
    else:
        messages.success(request, f"Queued research job #{job.pk} again.")
    return redirect("research:job_detail", pk=job.pk)


@login_required
def facebook_scan_create(request):
    if not request.user.is_staff:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("Facebook collection requires an operator account.")
    form = FacebookScanForm(request.POST or None, initial={"depth": "standard"})
    if request.method == "POST" and form.is_valid():
        job = form.save(commit=False)
        job.requested_by = request.user
        job.include_social = True
        job.plan = {"engine": "facebook_browser", "discovery_version": 1, "collector_version": 2}
        from apps.research.services.facebook_discovery import initialize_discovery
        with transaction.atomic():
            job.save()
            initialize_discovery(job, form.cleaned_data['sources'], form.manual_lines)
        try:
            queue_job(job)
        except Exception as exc:
            job.mark_finished(ResearchJob.Status.FAILED, str(exc)[:2000])
        return redirect("research:job_detail", pk=job.pk)
    return render(request, "research/job_form.html", {"form": form, "title": "One-time scan", "submit_label": "Start scan", "facebook_scan": True, "form_base": "research/facebook_base.html", "fb_nav": "settings"})

