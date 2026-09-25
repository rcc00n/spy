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


def normalize_update(payload: dict[str, Any]) -> RemoteJobUpdate:
    external_job_id = (
        payload.get("external_job_id")
        or payload.get("job_id")
        or payload.get("id")
        or ""
    )
    status = normalize_remote_status(payload.get("status") or payload.get("phase"))
    report = payload.get("report_markdown") or payload.get("report") or ""
    error = payload.get("error_message") or payload.get("error") or ""
    plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
    sources = payload.get("sources") if isinstance(payload.get("sources"), list) else []
    message = payload.get("message") or payload.get("detail") or ""
    return RemoteJobUpdate(
        status=status,
        external_job_id=str(external_job_id),
        plan=plan,
        report_markdown=str(report),
        error_message=str(error),
        sources=sources,
        message=str(message),
    )


class ResearchEngineClient:
    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        timeout_seconds: float | None = None,
    ):
        configured_url = base_url if base_url is not None else research_engine_base_url()
        self.base_url = configured_url.rstrip("/")
        self.token = token if token is not None else settings.RESEARCH_ENGINE_TOKEN
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else settings.RESEARCH_ENGINE_TIMEOUT_SECONDS
        )

    def ensure_configured(self) -> None:
        if not self.base_url:
            raise ResearchEngineError("RESEARCH_ENGINE_URL is not configured.")

    def submit_job(self, job) -> RemoteJobUpdate:
        self.ensure_configured()
        payload = {
            "portal_job_id": job.pk,
            "title": job.display_title,
            "query": job.query,
            "depth": job.depth,
            "include_social": job.include_social,
            "portal_base_url": settings.RESEARCH_PORTAL_PUBLIC_BASE_URL,
            "social_tool_base_url": settings.RESEARCH_SOCIAL_TOOL_BASE_URL,
            "metadata": {
                "requested_by": job.requested_by.get_username()
                if job.requested_by_id
                else "",
                "portal_task_id": job.task_id,
            },
        }
        return normalize_update(
            self._request_json("POST", "/v1/research/jobs", payload)
        )

    def get_job_status(self, external_job_id: str) -> RemoteJobUpdate:
        self.ensure_configured()
        quoted_id = quote(str(external_job_id), safe="")
        return normalize_update(
            self._request_json("GET", f"/v1/research/jobs/{quoted_id}")
        )

    def _request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        request = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw_body = response.read().decode("utf-8")
        except HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise ResearchEngineError(
                f"Research engine returned HTTP {exc.code}: {error_body[:1000]}"
            ) from exc
        except URLError as exc:
            raise ResearchEngineError(f"Research engine request failed: {exc}") from exc
        except TimeoutError as exc:
            raise ResearchEngineError("Research engine request timed out.") from exc

        if not raw_body:
            return {}
        try:
            parsed = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise ResearchEngineError(
                f"Research engine returned invalid JSON: {raw_body[:1000]}"
            ) from exc
        if not isinstance(parsed, dict):
            raise ResearchEngineError("Research engine response must be a JSON object.")
        return parsed
