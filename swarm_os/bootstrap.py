from swarm_os.import_lock import validate_import_graph
from swarm_os.services.control_plane import get_router
import os
import certifi
import ssl

def bootstrap():
    validate_import_graph()

    # Force all SSL connections in Python to use the certifi CA bundle
    # This fixes [SSL: CERTIFICATE_VERIFY_FAILED] for aiohttp/httpx on Windows Python 3.14
    os.environ["OLLAMA_API_BASE"] = "http://127.0.0.1:11434"
    if not hasattr(ssl, "_zenith_patched_ssl"):
        _original_create_default_context = ssl.create_default_context
        def custom_ssl_context(*args, **kwargs):
            kwargs['cafile'] = certifi.where()
            return _original_create_default_context(*args, **kwargs)
        ssl._create_default_https_context = custom_ssl_context
        ssl.create_default_context = custom_ssl_context
        ssl._zenith_patched_ssl = True

    return {
        "status": "locked",
        "router": "available"
    }

