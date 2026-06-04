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
