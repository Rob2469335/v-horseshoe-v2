import requests
import logging
import urllib3
from typing import Any
from organism_console.config import BACKEND_URL

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
log = logging.getLogger("zenith_cli")

def call_api(endpoint: str, method: str = "GET", payload: Any = None, stream: bool = False):
    """Robust API communication layer for ZENITH OS."""
    try:
        url = f"{BACKEND_URL}{endpoint}"
        if method == "GET":
            # Short timeout so the banner never freezes waiting on a slow backend
            return requests.get(url, timeout=3, verify=False)
        if stream:
            return requests.post(url, json=payload, timeout=(3.0, 300.0), stream=True, verify=False)
        return requests.post(url, json=payload, timeout=(3.0, 300.0), verify=False)
    except requests.exceptions.RequestException as e:
        log.debug(f"API call failed: {e}")
        return None
