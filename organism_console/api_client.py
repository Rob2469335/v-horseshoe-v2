import requests
import logging
import urllib3
from typing import Any
from organism_console.config import BACKEND_URL
from swarm_os.config.settings import settings

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
log = logging.getLogger("zenith_cli")

def call_api(endpoint: str, method: str = "GET", payload: Any = None, stream: bool = False):
    """Robust API communication layer for ZENITH OS."""
    try:
        url = f"{BACKEND_URL}{endpoint}"
        if method == "GET":
            # Short timeout so the banner never freezes waiting on a slow backend
            return requests.get(url, timeout=3, verify=settings.ssl_verify)
        if stream:
            return requests.post(url, json=payload, timeout=(3.0, 1200.0), stream=True, verify=settings.ssl_verify)
        return requests.post(url, json=payload, timeout=(3.0, 1200.0), verify=settings.ssl_verify)
    except requests.exceptions.RequestException as e:
        log.debug(f"API call failed: {e}")
        return None
