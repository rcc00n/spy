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
        self.ensure_configured()
        self._post(f"/pods/{self.pod_id}/start")

    def stop_pod(self) -> None:
        self.ensure_configured()
        self._post(f"/pods/{self.pod_id}/stop")

    def wait_for_engine(self) -> None:
        base_url = research_engine_base_url()
        if not base_url:
            raise RunPodLifecycleError(
                "RESEARCH_ENGINE_URL or RUNPOD_POD_ID/RUNPOD_ENGINE_PORT is required."
            )

        health_url = f"{base_url}{settings.RUNPOD_ENGINE_HEALTH_PATH}"
        deadline = time.monotonic() + settings.RUNPOD_START_TIMEOUT_SECONDS
        last_error = ""

        while time.monotonic() < deadline:
            try:
                request = Request(health_url, headers={"Accept": "application/json"})
                with urlopen(
                    request,
                    timeout=settings.RUNPOD_HEALTH_TIMEOUT_SECONDS,
                ) as response:
                    if 200 <= response.status < 300:
                        return
                    last_error = f"HTTP {response.status}"
            except HTTPError as exc:
                last_error = f"HTTP {exc.code}"
            except (TimeoutError, URLError) as exc:
                last_error = str(exc)

            time.sleep(settings.RUNPOD_HEALTH_POLL_SECONDS)

        raise RunPodLifecycleError(
            f"RunPod engine did not become healthy at {health_url}: {last_error}"
        )

    def _post(self, path: str) -> dict:
        url = f"https://rest.runpod.io/v1{path}"
        request = Request(
            url,
            data=b"",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=settings.RUNPOD_API_TIMEOUT_SECONDS) as response:
                raw_body = response.read().decode("utf-8")
        except HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise RunPodLifecycleError(
                f"RunPod API returned HTTP {exc.code}: {error_body[:1000]}"
            ) from exc
        except (TimeoutError, URLError) as exc:
            raise RunPodLifecycleError(f"RunPod API request failed: {exc}") from exc

        if not raw_body:
            return {}
        try:
            parsed = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise RunPodLifecycleError(
                f"RunPod API returned invalid JSON: {raw_body[:1000]}"
            ) from exc
        return parsed if isinstance(parsed, dict) else {}
