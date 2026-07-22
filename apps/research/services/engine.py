import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from django.conf import settings

from apps.research.services.runpod import research_engine_base_url


class ResearchEngineError(Exception):
    pass


@dataclass(frozen=True)
class RemoteJobUpdate:
    status: str | None
    external_job_id: str
    plan: dict[str, Any]
    report_markdown: str
    error_message: str
    sources: list[dict[str, Any]]
    message: str


REMOTE_STATUS_MAP = {
    "queued": "queued",
    "pending": "queued",
    "planning": "planning",
    "plan": "planning",
    "running": "collecting",
    "searching": "collecting",
    "collecting": "collecting",
    "web": "collecting",
    "social": "social_collecting",
    "social_collecting": "social_collecting",
    "critique": "critiquing",
    "critiquing": "critiquing",
    "critic": "critiquing",
    "analysis": "critiquing",
    "completed": "completed",
    "complete": "completed",
    "succeeded": "completed",
    "success": "completed",
    "done": "completed",
    "failed": "failed",
    "error": "failed",
    "cancelled": "cancelled",
    "canceled": "cancelled",
}


def normalize_remote_status(value: Any) -> str | None:
    if not value:
        return None
    return REMOTE_STATUS_MAP.get(str(value).strip().lower())


