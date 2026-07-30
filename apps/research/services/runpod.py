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


