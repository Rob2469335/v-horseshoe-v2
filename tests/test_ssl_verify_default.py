"""D6 regression: settings.ssl_verify must default to True (verification ON).

The established application posture (swarm_os/app/main.py) is SSL verification
enabled by default — `LITELLM_VERIFY_SSL` is set to "True" unless the operator
explicitly sets `DISABLE_SSL_VERIFICATION`. The `settings.ssl_verify` default
was False, so any consumer reading it (notably
runtime_v2/services/fallback_manager.get_http_client, which fetches HTTPS
provider catalogs) ran with certificate verification DISABLED — contradicting
the posture and reachable at runtime.

The env override `SWARM_SSL_VERIFY` must keep working in both directions.
"""

from __future__ import annotations

import os

from swarm_os.config.settings import Settings, _env_bool


def test_ssl_verify_field_default_matches_security_posture():
    """The dataclass field default (evaluated at import with .env loaded)
    must be True — the main.py posture — not False."""
    default = Settings.__dataclass_fields__["ssl_verify"].default
    assert default is True, (
        "settings.ssl_verify must default to True (SSL verification ON); "
        f"got {default!r} — this contradicts swarm_os/app/main.py's posture "
        "(LITELLM_VERIFY_SSL=True unless DISABLE_SSL_VERIFICATION is set)"
    )


def test_ssl_verify_env_override_still_disables(monkeypatch):
    """SWARM_SSL_VERIFY=0 must still disable verification (override preserved)."""
    monkeypatch.setenv("SWARM_SSL_VERIFY", "0")
    assert _env_bool("SWARM_SSL_VERIFY", True) is False


def test_ssl_verify_env_override_enables_and_unset_uses_default(monkeypatch):
    """SWARM_SSL_VERIFY=1 enables explicitly; unset falls back to the default."""
    monkeypatch.setenv("SWARM_SSL_VERIFY", "1")
    assert _env_bool("SWARM_SSL_VERIFY", True) is True
    monkeypatch.delenv("SWARM_SSL_VERIFY", raising=False)
    assert _env_bool("SWARM_SSL_VERIFY", True) is True


def test_no_env_leak_sets_ssl_verify_false():
    """Guard: neither .env (loaded at import) nor the ambient test environment
    may set SWARM_SSL_VERIFY to a falsy value that silently flips the default."""
    raw = os.environ.get("SWARM_SSL_VERIFY")
    assert raw is None or str(raw).lower() in ("1", "true", "yes", "t", "y"), (
        f"SWARM_SSL_VERIFY={raw!r} in the test environment would disable "
        "SSL verification for settings consumers"
    )
