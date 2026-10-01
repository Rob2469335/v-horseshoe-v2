"""Experiment J model identity and inference topology — preregistered provenance.

Why this module exists
----------------------
``EXPERIMENT_J.md`` requires model provenance as a REQUIRED RECORD:

* ``:125`` — "Model identity recorded | ``model_name`` in trajectory;
  **GGUF SHA256 verified at startup**"
* ``:126`` — "LoRA identity recorded | GGUF contains LoRA; **SHA256 verified**"
* ``:144`` — "Model identity/hash | REQUIRED RECORD | **GGUF SHA256 logged at startup**"
* ``:145`` — "LoRA identity/hash | REQUIRED RECORD | Same as model"

No mechanism implemented that requirement. ``FrozenArtifact.model_name``
(``f2_freeze.py:93``) is a free-form string defaulting to ``""`` and is
caller-supplied, so it names a model but does not prove which bytes were
served. This module implements the requirement.

The expected values below are PREREGISTERED, not derived from a running
endpoint:

* ``docs/RUNPOD_RUNBOOK.md:20`` — "``qwen_train/robs4b_q4km.gguf`` (SHA256
  ``65202F372110DDE854B40CE15DCD1B6AB56A1FE9EA542B84B6A9CC745B242D41``,
  2.71 GB)"
* ``docs/RUNPOD_RUNBOOK.md:193`` — "``65202F37...B242D41``"
* ``AGENTS_LEGACY.md:6193`` — "Download/verify ``robs4b_q4km.gguf`` (size:
  2708803840, SHA-256 prefix: 65202f37)"

Both were documented before this module and independently recomputed from the
live GGUF, matching exactly. The values live here, in one version-controlled
place, so that changing an expected identity is a visible git diff — which is
what preregistration discipline means.

Two distinct identities, deliberately NOT interchangeable
-------------------------------------------------------
* **Model identity** — the GGUF bytes. Proven by SHA-256 over the file named
  by the live ``/props`` ``model_path``. This is the F0 requirement.
* **Configuration fingerprint** — ``props_hash`` over
  ``build_info|model_path|n_ctx|total_slots``. Detects endpoint configuration
  drift (context size, slot count, build). It is a SECONDARY runtime-integrity
  check and must never substitute for the SHA-256.

Inference topology
------------------
``model_router.py:90-98`` documents two legitimate topologies: a LOCAL
``llama.exe`` owning ``:8079``, and a RUNPOD SSH tunnel owning ``:8079`` when
``SWARM_ROUTER_PINNED=1``. ``status.pinned`` at the router's status endpoint is
the authoritative discriminator, and the mode is recorded as provenance.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# PREREGISTERED expected identity.
#
# Changing any value here changes which model Experiment J may legally use and
# MUST be an explicit, auditable act. See the module docstring for provenance.
# ---------------------------------------------------------------------------

#: Alias the authorized local model is served under (``/props.model_alias``).
EXPECTED_MODEL_ALIAS = "robs4b"

#: Repo-relative GGUF identity of the authorized local model.
EXPECTED_GGUF_RELATIVE_PATH = "qwen_train/robs4b_q4km.gguf"

#: Expected SHA-256 of that GGUF. Preregistered in RUNPOD_RUNBOOK.md:20 and
#: AGENTS_LEGACY.md:6193; independently recomputed and matched.
EXPECTED_GGUF_SHA256 = (
    "65202f372110dde854b40ce15dcd1b6ab56a1fe9ea542b84b6a9cc745b242d41"
)

#: Byte size recorded alongside the hash in AGENTS_LEGACY.md:6193.
EXPECTED_GGUF_SIZE_BYTES = 2708803840

#: Secondary endpoint CONFIGURATION fingerprint for the local topology, recorded
#: by the authorized operator on 2026-09-30 from the live endpoint. This is NOT
#: the model identity -- see the module docstring.
EXPECTED_LOCAL_PROPS_FINGERPRINT = "23408a87ffe0f2cb"


# ---------------------------------------------------------------------------
# Topology
# ---------------------------------------------------------------------------

#: Router's endpoint status port (``model_router.py:114``,
#: ``SWARM_ROUTER_STATUS_PORT``, default 8095).
DEFAULT_STATUS_PORT = 8095

#: Inference endpoint port (``model_router.py:162`` BACKEND_URL).
DEFAULT_INFERENCE_PORT = 8079

LOCAL = "local"
RUNPOD = "runpod"


class ModelIdentityError(RuntimeError):
    """Raised when model identity cannot be verified. Always fails closed."""


@dataclass(frozen=True)
class VerifiedIdentity:
    """Verified model identity plus the configuration fingerprint."""

    model_alias: str
    gguf_path: str
    gguf_sha256: str
    gguf_size_bytes: int
    props_fingerprint: str
    topology: str
    endpoint_owner: str
    boot_id: str = ""
    extras: dict = field(default_factory=dict)

    def as_provenance(self) -> dict:
        """Provenance record for experiment/rollout evidence."""
        return {
            "model_alias": self.model_alias,
            "model_gguf_path": self.gguf_path,
            "model_gguf_sha256": self.gguf_sha256,
            "model_gguf_size_bytes": self.gguf_size_bytes,
            "props_fingerprint": self.props_fingerprint,
            "inference_topology": self.topology,
            "inference_endpoint_owner": self.endpoint_owner,
            "router_boot_id": self.boot_id,
        }


def sha256_file(path: Path) -> str:
    """Stream a SHA-256 over ``path``. Raises on any read failure."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
    except OSError as exc:
        raise ModelIdentityError(f"cannot read GGUF {path}: {exc}") from exc
    return h.hexdigest()


def props_fingerprint(props: dict) -> str:
    """Configuration fingerprint over build/model/context/slot.

    SECONDARY integrity signal only. A matching fingerprint does NOT prove
    which model bytes were served -- only ``verify_model_identity`` does that.
    """
    return hashlib.sha256(
        f"{props.get('build_info', '')}|{props.get('model_path', '')}"
        f"|{props.get('default_generation_settings', {}).get('n_ctx', 0)}"
        f"|{props.get('total_slots', 0)}".encode()
    ).hexdigest()[:16]


def _resolve_gguf(model_path: str, root: Path) -> Path:
    """Resolve ``/props.model_path`` to the EXACT artifact being served.

    Invariant: the file returned here is the artifact the endpoint identified as
    the model being served, so hashing it proves something about the served
    model. Anything else is a self-validating substitution.

    Therefore there is deliberately NO fallback to the preregistered GGUF. A
    path that is not a readable local file cannot be hashed, and a remote path
    (RunPod reports a pod-side path) means the served artifact is not
    accessible from here -- so verification FAILS CLOSED rather than hashing an
    unrelated local file. Note a matching BASENAME is not evidence: a pod can
    serve different content under the authorized filename, and hashing the local
    file would then attest to a model that was never served.
    """
    raw = (model_path or "").strip()
    if not raw:
        raise ModelIdentityError(
            "endpoint /props reported no model_path; the served artifact cannot "
            "be identified"
        )
    direct = Path(raw)
    if direct.is_file():
        return direct

    # Not locally readable. Distinguish "named a model we did not authorize"
    # (clear diagnosis) from "remote/unavailable" (capability gap) so the
    # failure says why rather than implying substitution.
    match = re.search(r"([^/\\]+\.gguf)$", raw)
    if match and match.group(1).lower() != Path(
        EXPECTED_GGUF_RELATIVE_PATH
    ).name.lower():
        raise ModelIdentityError(
            f"endpoint serves unauthorized model {match.group(1)!r}; "
            f"authorized model is {Path(EXPECTED_GGUF_RELATIVE_PATH).name!r}"
        )
    raise ModelIdentityError(
        f"served model_path {raw!r} is not accessible locally, so its identity "
        f"cannot be verified. Refusing to substitute "
        f"{EXPECTED_GGUF_RELATIVE_PATH!r}: a basename match is not proof of "
        f"content. A remote endpoint must expose its own model digest before "
        f"Experiment J identity verification can pass for it."
    )


def verify_model_identity(
    props: dict,
    *,
    topology: str,
    endpoint_owner: str = "",
    boot_id: str = "",
    expected_alias: str | None = None,
    expected_sha256: str | None = None,
    expected_fingerprint: str | None = None,
    repo_root: Path | None = None,
) -> VerifiedIdentity:
    """Verify the SERVED model against the PREREGISTERED identity.

    Order is deliberate: model identity (SHA-256 over the served GGUF) is
    checked FIRST and independently, so a matching configuration fingerprint can
    never stand in for it.

    Expected values resolve from this module's preregistered constants at CALL
    time (so production always uses the authorized value, while a test may
    supply an explicit expectation). Raises ``ModelIdentityError`` on any of:
    missing/unreadable GGUF, alias mismatch, SHA-256 mismatch, size mismatch,
    or -- when ``expected_fingerprint`` is supplied -- configuration drift.
    """
    if expected_alias is None:
        expected_alias = EXPECTED_MODEL_ALIAS
    if expected_sha256 is None:
        expected_sha256 = EXPECTED_GGUF_SHA256
    if topology not in (LOCAL, RUNPOD):
        raise ModelIdentityError(f"unknown inference topology: {topology!r}")

    actual_alias = str(props.get("model_alias") or "").strip()
    if not actual_alias:
        raise ModelIdentityError("endpoint /props reported no model_alias")
    if actual_alias != expected_alias:
        raise ModelIdentityError(
            f"model alias mismatch: expected {expected_alias!r}, "
            f"endpoint serves {actual_alias!r}"
        )

    root = Path(repo_root) if repo_root else Path(__file__).parent.parent.parent
    gguf = _resolve_gguf(str(props.get("model_path") or ""), root)

    size = gguf.stat().st_size
    if size != EXPECTED_GGUF_SIZE_BYTES:
        raise ModelIdentityError(
            f"GGUF size mismatch for {gguf.name}: expected "
            f"{EXPECTED_GGUF_SIZE_BYTES}, got {size}"
        )

    actual_sha = sha256_file(gguf)
    if actual_sha.lower() != expected_sha256.lower():
        raise ModelIdentityError(
            f"GGUF SHA-256 mismatch: expected {expected_sha256}, got {actual_sha}"
        )

    fingerprint = props_fingerprint(props)
    if expected_fingerprint is not None:
        if fingerprint != expected_fingerprint:
            raise ModelIdentityError(
                f"endpoint configuration drift: expected props fingerprint "
                f"{expected_fingerprint}, got {fingerprint}"
            )

    return VerifiedIdentity(
        model_alias=actual_alias,
        gguf_path=str(gguf),
        gguf_sha256=actual_sha.lower(),
        gguf_size_bytes=size,
        props_fingerprint=fingerprint,
        topology=topology,
        endpoint_owner=endpoint_owner,
        boot_id=boot_id,
    )


def endpoint_owner(tunnel_port: int = DEFAULT_INFERENCE_PORT) -> str:
    """Best-effort name of the process LISTENING on the inference port.

    RunPod requires ``ssh.exe``; LOCAL requires a local server (``llama``).
    Recorded as provenance in both modes. Returns ``""`` when undeterminable.
    """
    try:
        import psutil

        for conn in psutil.net_connections(kind="inet"):
            if conn.laddr and conn.laddr.port == tunnel_port and conn.status == "LISTEN":
                try:
                    return psutil.Process(conn.pid).name()
                except Exception:  # noqa: BLE001 - provenance is best effort
                    return f"pid:{conn.pid}"
    except Exception:  # noqa: BLE001 - provenance must never fail the gate
        return ""
    return ""
