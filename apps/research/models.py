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
