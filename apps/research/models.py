from django.conf import settings
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone


class ResearchJob(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        PLANNING = "planning", "Planning"
        COLLECTING = "collecting", "Collecting"
        SOCIAL_COLLECTING = "social_collecting", "Social collecting"
        CRITIQUING = "critiquing", "Critiquing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    class Depth(models.TextChoices):
        QUICK = "quick", "Quick"
        STANDARD = "standard", "Standard"
        DEEP = "deep", "Deep"

    TERMINAL_STATUSES = {
        Status.COMPLETED,
        Status.FAILED,
        Status.CANCELLED,
    }

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="research_jobs",
    )
    title = models.CharField(max_length=255, blank=True)
    query = models.TextField()
    depth = models.CharField(
        max_length=20,
        choices=Depth.choices,
        default=Depth.STANDARD,
    )
    include_social = models.BooleanField(default=True)
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.QUEUED,
    )
    monitor = models.ForeignKey('FacebookMonitor', null=True, blank=True, on_delete=models.SET_NULL, related_name='jobs')
    monitor_lane = models.CharField(max_length=12, blank=True, db_default='')
    task_id = models.CharField(max_length=255, blank=True)
    external_job_id = models.CharField(max_length=255, blank=True)
    plan = models.JSONField(default=dict, blank=True)
    report_markdown = models.TextField(blank=True)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["requested_by", "created_at"]),
            models.Index(fields=["external_job_id"]),
        ]

    def __str__(self) -> str:
        return f"Research #{self.pk}: {self.display_title}"

    @property
