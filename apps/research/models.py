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
    def display_title(self) -> str:
        return self.title or self.query[:80]

    @property
    def is_terminal(self) -> bool:
        return self.status in self.TERMINAL_STATUSES

    def mark_started(self, status: str = Status.PLANNING) -> None:
        self.status = status
        self.started_at = self.started_at or timezone.now()
        self.error_message = ""
        self.save(update_fields=["status", "started_at", "error_message", "updated_at"])

    def mark_finished(self, status: str, error_message: str = "") -> None:
        self.status = status
        self.completed_at = timezone.now()
        self.error_message = error_message
        self.save(update_fields=["status", "completed_at", "error_message", "updated_at"])


class ResearchEvent(models.Model):
    class Level(models.TextChoices):
        INFO = "info", "Info"
        WARNING = "warning", "Warning"
        ERROR = "error", "Error"

    job = models.ForeignKey(
        ResearchJob,
        on_delete=models.CASCADE,
        related_name="events",
    )
    level = models.CharField(max_length=20, choices=Level.choices, default=Level.INFO)
    message = models.TextField()
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(fields=["job", "created_at"]),
            models.Index(fields=["level", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_level_display()} for research #{self.job_id}"


class ResearchSource(models.Model):
    class SourceType(models.TextChoices):
        WEB = "web", "Web"
        SOCIAL = "social", "Social"
        DOCUMENT = "document", "Document"
        OTHER = "other", "Other"

    job = models.ForeignKey(
        ResearchJob,
        on_delete=models.CASCADE,
        related_name="sources",
    )
    source_type = models.CharField(
        max_length=30,
        choices=SourceType.choices,
        default=SourceType.OTHER,
    )
    title = models.CharField(max_length=500, blank=True)
    url = models.URLField(max_length=2000, blank=True)
    excerpt = models.TextField(blank=True)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["source_type", "title", "id"]
        indexes = [
            models.Index(fields=["job", "source_type"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self) -> str:
        return self.title or self.url or f"{self.source_type} source"



class FacebookPost(models.Model):
    """Public evidence shared across scans; timestamps are observation times."""
    url = models.URLField(max_length=2000)
    url_hash = models.CharField(max_length=64, unique=True)
    refresh_queued_at = models.DateTimeField(null=True, blank=True)
    backfill_queued_at = models.DateTimeField(null=True, blank=True)
    text = models.TextField(blank=True)
    public_verified = models.BooleanField(default=False)
    mentions_pcl = models.BooleanField(default=False)
    first_seen_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now)


class FacebookComment(models.Model):
    post = models.ForeignKey(FacebookPost, on_delete=models.CASCADE, related_name="comments")
    facebook_id = models.CharField(max_length=255)
    parent_id = models.CharField(max_length=255, blank=True)
    url = models.URLField(max_length=2500)
    text = models.TextField(blank=True)
    has_media = models.BooleanField(default=False)
    mentions_pcl = models.BooleanField(default=False)
    first_seen_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["post", "facebook_id"], name="fb_comment_post_id_unique")]
