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
