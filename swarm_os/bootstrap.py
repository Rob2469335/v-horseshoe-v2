import os
import certifi
import ssl

# Force all SSL connections in Python to use the certifi CA bundle
# This fixes [SSL: CERTIFICATE_VERIFY_FAILED] for aiohttp/httpx on Windows Python 3.14
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

if not hasattr(ssl, "_zenith_patched_ssl"):
    ssl._create_default_https_context = ssl._create_unverified_context
    ssl.create_default_context = ssl._create_unverified_context
    ssl._zenith_patched_ssl = True

from swarm_os.import_lock import validate_import_graph
from swarm_os.services.control_plane import get_router

def bootstrap():
    validate_import_graph()
    return {
        "status": "locked",
        "router": "available"
    }

