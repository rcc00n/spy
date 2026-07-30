import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings


class RunPodLifecycleError(Exception):
    pass


def research_engine_base_url() -> str:
    configured_url = settings.RESEARCH_ENGINE_URL.strip()
    if configured_url:
        return configured_url.rstrip("/")

    pod_id = settings.RUNPOD_POD_ID.strip()
    if pod_id and settings.RUNPOD_ENGINE_PORT:
        return f"https://{pod_id}-{settings.RUNPOD_ENGINE_PORT}.proxy.runpod.net"

    return ""


class RunPodLifecycleClient:
    def __init__(
        self,
        api_key: str | None = None,
        pod_id: str | None = None,
    ):
        self.api_key = api_key if api_key is not None else settings.RUNPOD_API_KEY
        self.pod_id = pod_id if pod_id is not None else settings.RUNPOD_POD_ID

    @property
    def autostart_enabled(self) -> bool:
        return settings.RUNPOD_AUTOSTART_ENABLED

    @property
    def autostop_enabled(self) -> bool:
        return settings.RUNPOD_AUTOSTOP_AFTER_JOB

    def ensure_configured(self) -> None:
        if not self.api_key:
            raise RunPodLifecycleError("RUNPOD_API_KEY is not configured.")
        if not self.pod_id:
            raise RunPodLifecycleError("RUNPOD_POD_ID is not configured.")

    def start_pod(self) -> None:
