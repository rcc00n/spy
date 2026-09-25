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


class FacebookCommentRevision(models.Model):
    comment = models.ForeignKey(FacebookComment, on_delete=models.CASCADE, related_name="revisions")
    text = models.TextField(blank=True)
    has_media = models.BooleanField(default=False)
    observed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["observed_at", "id"]


class FacebookThreadWork(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Reading"
        SAMPLED = "sampled", "No further comments observed"
        POST_ONLY = "post_only", "Post only"
        PARTIAL = "partial", "Partial — continue available"
        FAILED = "failed", "Extraction failed"
        BLOCKED = "blocked", "Access stopped"

    job = models.ForeignKey(ResearchJob, on_delete=models.CASCADE, related_name="facebook_threads")
    post = models.ForeignKey(FacebookPost, on_delete=models.CASCADE, related_name="work_items")
    comments = models.ManyToManyField(FacebookComment, blank=True, related_name="work_items")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    queries = models.JSONField(default=list, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    cycle_attempts = models.PositiveIntegerField(default=0)
    checkpoint = models.JSONField(default=dict, blank=True)
    coverage = models.CharField(max_length=80, blank=True)
    error = models.TextField(blank=True)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["job", "post"], name="fb_work_job_post_unique")]
        indexes = [models.Index(fields=["status", "next_attempt_at"])]


class FacebookDiscoverySource(models.Model):
    class Kind(models.TextChoices):
        PAGE = 'page', 'Page'
        GROUP = 'group', 'Public group'
        SEARCH = 'search', 'Search phrase'

    name = models.CharField(max_length=160)
    kind = models.CharField(max_length=12, choices=Kind.choices)
    target = models.CharField(max_length=500)
    enabled = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['kind', 'name']
        constraints = [models.UniqueConstraint(fields=['kind', 'target'], name='fb_discovery_source_unique')]

    def __str__(self):
        return f'{self.get_kind_display()}: {self.name}'


class FacebookDiscoveryRun(models.Model):
    job = models.ForeignKey(ResearchJob, on_delete=models.CASCADE, related_name='facebook_discoveries')
    source = models.ForeignKey(FacebookDiscoverySource, null=True, blank=True, on_delete=models.SET_NULL, related_name='runs')
    # Snapshot configuration: editing/disabling a watchlist source affects future scans.
    name = models.CharField(max_length=160)
    kind = models.CharField(max_length=12)
    target = models.CharField(max_length=500)
    status = models.CharField(max_length=16, default='queued')
    coverage = models.CharField(max_length=80, blank=True)
    error = models.TextField(blank=True)
    attempts = models.PositiveIntegerField(default=0)
    cycle_attempts = models.PositiveIntegerField(default=0)
    posts = models.ManyToManyField(FacebookPost, blank=True, related_name='discoveries')
    next_attempt_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['pk']
        constraints = [models.UniqueConstraint(fields=['job', 'kind', 'target'], name='fb_discovery_run_unique')]
        indexes = [models.Index(fields=['status', 'next_attempt_at'])]


class FacebookMonitor(models.Model):
    """Single operator-controlled schedule sharing the site's browser session."""
    enabled = models.BooleanField(default=False)
    pause_reason = models.TextField(blank=True)
    discovery_hours = models.PositiveIntegerField(default=6, validators=[MinValueValidator(1), MaxValueValidator(168)])
    refresh_hours = models.PositiveIntegerField(default=2, validators=[MinValueValidator(1), MaxValueValidator(168)])
    backfill_hours = models.PositiveIntegerField(default=12, validators=[MinValueValidator(1), MaxValueValidator(168)])
    refresh_batch = models.PositiveIntegerField(default=12, validators=[MinValueValidator(1), MaxValueValidator(50)])
    backfill_batch = models.PositiveIntegerField(default=4, validators=[MinValueValidator(1), MaxValueValidator(20)])
    discovery_due_at = models.DateTimeField(default=timezone.now)
    refresh_due_at = models.DateTimeField(default=timezone.now)
    backfill_due_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(pk=1), name='fb_monitor_singleton')]


class FacebookReview(models.Model):
    """Personal reading state; independent of collection and shared evidence."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    post = models.ForeignKey(FacebookPost, on_delete=models.CASCADE, related_name='reviews')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    saved = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'post'], name='fb_review_user_post_unique')]
