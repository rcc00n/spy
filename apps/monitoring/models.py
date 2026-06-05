import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


def facebook_credential_fernet() -> Fernet:
    configured_key = getattr(settings, "FACEBOOK_CREDENTIAL_ENCRYPTION_KEY", "")
    if configured_key:
        return Fernet(configured_key.encode("utf-8"))

    derived_key = base64.urlsafe_b64encode(
        hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
    )
    return Fernet(derived_key)


class MonitoredAccount(models.Model):
    class Platform(models.TextChoices):
        FACEBOOK = "facebook", "Facebook"
        INSTAGRAM = "instagram", "Instagram"

    platform = models.CharField(max_length=20, choices=Platform.choices)
    account_url = models.URLField(max_length=1000, unique=True)
    account_name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    check_interval_minutes = models.PositiveIntegerField(
        default=60,
        validators=[MinValueValidator(5)],
    )
    max_posts_per_check = models.PositiveIntegerField(
        default=5,
        validators=[MinValueValidator(1), MaxValueValidator(20)],
    )
    scroll_rounds = models.PositiveIntegerField(
        default=2,
        validators=[MinValueValidator(0), MaxValueValidator(5)],
    )
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_status = models.CharField(max_length=50, blank=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["platform", "account_name"]
        indexes = [
            models.Index(fields=["platform", "is_active"]),
            models.Index(fields=["last_checked_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_platform_display()}: {self.account_name}"

    def is_due(self, at_time=None) -> bool:
        if not self.is_active:
            return False
        if self.last_checked_at is None:
            return True
        at_time = at_time or timezone.now()
        due_at = self.last_checked_at + timezone.timedelta(
            minutes=self.check_interval_minutes
        )
        return due_at <= at_time


class Keyword(models.Model):
    phrase = models.CharField(max_length=255, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["phrase"]
        indexes = [
            models.Index(fields=["is_active"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self) -> str:
        return self.phrase


class Post(models.Model):
    monitored_account = models.ForeignKey(
        MonitoredAccount,
        on_delete=models.CASCADE,
        related_name="posts",
    )
    platform = models.CharField(max_length=20, choices=MonitoredAccount.Platform.choices)
    external_post_id = models.CharField(max_length=255)
    post_url = models.URLField(max_length=1000, blank=True)
    text = models.TextField()
    published_at = models.DateTimeField(null=True, blank=True)
    first_seen_at = models.DateTimeField(auto_now_add=True)
    raw_snapshot = models.TextField(blank=True)

    class Meta:
        ordering = ["-first_seen_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["platform", "external_post_id"],
                name="unique_platform_external_post",
            )
        ]
        indexes = [
            models.Index(fields=["platform", "first_seen_at"]),
            models.Index(fields=["monitored_account", "first_seen_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.platform}:{self.external_post_id}"


class PostKeywordMatch(models.Model):
    post = models.ForeignKey(
        Post,
        on_delete=models.CASCADE,
        related_name="keyword_matches",
    )
    keyword = models.ForeignKey(
        Keyword,
        on_delete=models.CASCADE,
        related_name="post_matches",
    )
    matched_text_preview = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    telegram_sent = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["post", "keyword"],
                name="unique_post_keyword_match",
            )
        ]
        indexes = [
            models.Index(fields=["created_at"]),
            models.Index(fields=["telegram_sent"]),
        ]

    def __str__(self) -> str:
        return f"{self.keyword.phrase} in {self.post}"


class CheckRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        ERROR = "error", "Error"
        AUTH_REQUIRED = "auth_required", "Auth required"
        SKIPPED = "skipped", "Skipped"

    monitored_account = models.ForeignKey(
        MonitoredAccount,
        on_delete=models.CASCADE,
        related_name="check_runs",
    )
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.RUNNING,
    )
    error_message = models.TextField(blank=True)
    posts_found = models.PositiveIntegerField(default=0)
    new_posts_found = models.PositiveIntegerField(default=0)
    matches_found = models.PositiveIntegerField(default=0)
    final_url = models.URLField(max_length=2000, blank=True)
    route_url = models.URLField(max_length=2000, blank=True)
    page_title = models.CharField(max_length=500, blank=True)
    http_status_code = models.PositiveIntegerField(null=True, blank=True)
    facebook_state = models.CharField(max_length=100, blank=True)
    article_count = models.PositiveIntegerField(default=0)
    link_count = models.PositiveIntegerField(default=0)
    post_link_count = models.PositiveIntegerField(default=0)
    diagnostic_text = models.TextField(blank=True)
    diagnostic_html_snapshot = models.TextField(blank=True)
    screenshot_path = models.CharField(max_length=1000, blank=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["status", "started_at"]),
            models.Index(fields=["monitored_account", "started_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.monitored_account} {self.status} at {self.started_at:%Y-%m-%d %H:%M}"

    @property
    def duration_seconds(self):
        if not self.finished_at:
            return None
        return (self.finished_at - self.started_at).total_seconds()


class CheckRunPost(models.Model):
    class Status(models.TextChoices):
        STORED = "stored", "Stored"
        SKIPPED = "skipped", "Skipped"

    check_run = models.ForeignKey(
        CheckRun,
        on_delete=models.CASCADE,
        related_name="observed_posts",
    )
    post = models.ForeignKey(
        Post,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="check_observations",
    )
    sequence = models.PositiveIntegerField()
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.STORED,
    )
    skip_reason = models.CharField(max_length=255, blank=True)
    external_post_id = models.CharField(max_length=255)
    post_url = models.URLField(max_length=1000, blank=True)
    source_type = models.CharField(max_length=100, blank=True)
    text = models.TextField()
    raw_snapshot = models.TextField(blank=True)
    is_new = models.BooleanField(default=False)
    observed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["check_run", "sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["check_run", "sequence"],
                name="unique_check_run_post_sequence",
            )
        ]
        indexes = [
            models.Index(fields=["check_run", "sequence"]),
            models.Index(fields=["status", "observed_at"]),
            models.Index(fields=["external_post_id"]),
        ]

    def __str__(self) -> str:
        return f"run={self.check_run_id} seq={self.sequence} {self.status}"


class ManualCheckJob(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        ERROR = "error", "Error"

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="manual_check_jobs",
    )
    account = models.ForeignKey(
        MonitoredAccount,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="manual_check_jobs",
    )
    post_limit = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(
