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
