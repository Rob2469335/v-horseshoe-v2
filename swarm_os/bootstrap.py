import ssl

try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass

if not hasattr(ssl, "_zenith_patched_ssl"):
    try:
        ssl._create_default_https_context = ssl._create_unverified_context
        ssl.create_default_context = ssl._create_unverified_context
    except Exception:
        pass
    ssl._zenith_patched_ssl = True

from swarm_os.import_lock import validate_import_graph


def bootstrap():
    validate_import_graph()
    return {"status": "locked", "router": "available"}
