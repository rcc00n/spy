import time

from celery import shared_task
from django.conf import settings
from django.db import transaction

from apps.research.models import ResearchEvent, ResearchJob, ResearchSource
from apps.research.services.engine import ResearchEngineClient, ResearchEngineError
from apps.research.services.runpod import RunPodLifecycleClient


def record_event(
