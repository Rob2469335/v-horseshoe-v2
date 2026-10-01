"""Experiment J model identity + dual inference topology (LOCAL / RUNPOD).

Covers:

* **Model identity is the GGUF SHA-256**, preregistered in
  ``docs/RUNPOD_RUNBOOK.md:20`` and ``AGENTS_LEGACY.md:6193``, implemented per
  F0 ``EXPERIMENT_J.md:125``/``:144``/``:145``.
* **No self-validating substitution.** The SHA is computed over the EXACT
  artifact ``/props.model_path`` identifies. A remote/unreadable path fails
  closed rather than falling back to a local file of the same name.
* **The ``/props`` fingerprint is configuration integrity only** and can never
  substitute for the hash.
* **Two topologies**: LOCAL (local llama.exe on :8079, no pin) and RUNPOD (SSH
  tunnel, operator pin required). Topology requires a strict bool AND agreement
  with ``SWARM_ROUTER_PINNED``; the two contracts cannot cross.

Synthetic tiny GGUF files are used, so no multi-GB model is hashed and no live
endpoint is required.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from swarm_os.services.experiment_model_identity import (  # noqa: E402
    EXPECTED_GGUF_SHA256,
    EXPECTED_GGUF_SIZE_BYTES,
    EXPECTED_MODEL_ALIAS,
    LOCAL,
    ModelIdentityError,
    RUNPOD,
    props_fingerprint,
    sha256_file,
)
from swarm_os.services.prompt_repairer import BenchmarkEvaluator  # noqa: E402


def _fake_gguf(tmp_path: Path, name: str = "robs4b_q4km.gguf"):
    p = tmp_path / name
    p.write_bytes(b"NOT-A-REAL-GGUF-" + b"x" * 512)
    return p, sha256_file(p), p.stat().st_size


def _props(path, *, alias: str = EXPECTED_MODEL_ALIAS, n_ctx: int = 16384,
           slots: int = 1, build: str = "b10107-c0bc8591e") -> dict:
    return {
        "model_path": path if isinstance(path, str) else str(path),
        "model_alias": alias,
        "build_info": build,
        "total_slots": slots,
        "default_generation_settings": {"n_ctx": n_ctx},
    }


class _Resp:
    def __init__(self, data, status_code=200):
        self._d = data
        self.status_code = status_code

    def json(self):
        return self._d


@pytest.fixture
def identity(tmp_path, monkeypatch):
    """Pin preregistered size+SHA to a synthetic file so tests are runnable."""
    import swarm_os.services.experiment_model_identity as mod

    path, sha, size = _fake_gguf(tmp_path)
    monkeypatch.setattr(mod, "EXPECTED_GGUF_SIZE_BYTES", size)
    monkeypatch.setattr(mod, "EXPECTED_GGUF_SHA256", sha)
    return mod, path, sha


# ---------------------------------------------------------------------------
# Model identity: the GGUF SHA-256 is the authority
# ---------------------------------------------------------------------------


class TestModelIdentity:
    def test_correct_identity_passes(self, identity):
        mod, path, sha = identity
        v = mod.verify_model_identity(_props(path), topology=LOCAL, repo_root=_REPO_ROOT)
        assert v.gguf_sha256 == sha and v.model_alias == EXPECTED_MODEL_ALIAS
        assert v.topology == LOCAL

    def test_wrong_gguf_sha_fails_closed(self, identity):
        mod, path, _ = identity
        with pytest.raises(ModelIdentityError, match="SHA-256 mismatch"):
            mod.verify_model_identity(_props(path), topology=LOCAL,
                                      expected_sha256="0" * 64, repo_root=_REPO_ROOT)

    def test_wrong_alias_fails_closed(self, identity):
        mod, path, _ = identity
        with pytest.raises(ModelIdentityError, match="alias mismatch"):
            mod.verify_model_identity(_props(path, alias="some-other-model"),
                                      topology=LOCAL, repo_root=_REPO_ROOT)

    def test_missing_alias_fails_closed(self, identity):
        mod, path, _ = identity
        with pytest.raises(ModelIdentityError, match="no model_alias"):
            mod.verify_model_identity(_props(path, alias=""), topology=LOCAL,
                                      repo_root=_REPO_ROOT)

    def test_unauthorized_name_fails_closed(self, identity, tmp_path):
        mod, path, _ = identity
        alien = path.parent / "some_other_model.gguf"
        with pytest.raises(ModelIdentityError, match="unauthorized model"):
            mod.verify_model_identity(_props(alien), topology=LOCAL, repo_root=tmp_path)

    def test_empty_model_path_fails_closed(self, identity):
        mod, path, _ = identity
        p = _props(path)
        p["model_path"] = ""
        with pytest.raises(ModelIdentityError, match="no model_path"):
            mod.verify_model_identity(p, topology=LOCAL, repo_root=_REPO_ROOT)

    def test_remote_authorized_named_path_is_not_substituted(self, identity, tmp_path):
        """REGRESSION for the discovered attack: no self-validating fallback.

        Endpoint reports a REMOTE path ending in the AUTHORIZED filename (what a
        pod does), a local authorized GGUF exists, and the remote artifact is
        not accessible here. Verification MUST fail AND the local GGUF must NOT
        be hashed -- otherwise the SHA would attest to a model never served.
        """
        mod, path, _ = identity
        remote = "/workspace/runtime/robs4b_q4km.gguf"
        local_copy = tmp_path / "qwen_train" / "robs4b_q4km.gguf"
        local_copy.parent.mkdir(parents=True, exist_ok=True)
        local_copy.write_bytes(b"NOT-A-REAL-GGUF-" + b"x" * 512)

        calls = []
        real_sha = mod.sha256_file
        mod.sha256_file = lambda p: (calls.append(Path(p)), real_sha(p))[1]
        try:
            with pytest.raises(ModelIdentityError, match="not accessible locally"):
                mod.verify_model_identity(_props(remote), topology=RUNPOD,
                                          repo_root=tmp_path)
        finally:
            mod.sha256_file = real_sha
        assert local_copy not in calls, "local GGUF was hashed as a substitute"
        assert not any(str(c).endswith("qwen_train/robs4b_q4km.gguf") for c in calls)

    def test_wrong_fingerprint_fails_closed(self, identity):
        mod, path, _ = identity
        with pytest.raises(ModelIdentityError, match="configuration drift"):
            mod.verify_model_identity(_props(path), topology=LOCAL,
                                      expected_fingerprint="deadbeefdeadbeef",
                                      repo_root=_REPO_ROOT)

    def test_matching_fingerprint_passes(self, identity):
        mod, path, _ = identity
        p = _props(path)
        v = mod.verify_model_identity(p, topology=LOCAL,
                                      expected_fingerprint=props_fingerprint(p),
                                      repo_root=_REPO_ROOT)
        assert v.props_fingerprint == props_fingerprint(p)

    def test_fingerprint_cannot_substitute_for_hash(self, identity):
        mod, path, _ = identity
        p = _props(path)
        with pytest.raises(ModelIdentityError, match="SHA-256 mismatch"):
            mod.verify_model_identity(p, topology=LOCAL, expected_sha256="1" * 64,
                                      expected_fingerprint=props_fingerprint(p),
                                      repo_root=_REPO_ROOT)

    def test_identity_checked_before_fingerprint(self, identity):
        mod, path, _ = identity
        with pytest.raises(ModelIdentityError, match="SHA-256 mismatch"):
            mod.verify_model_identity(_props(path), topology=LOCAL,
                                      expected_sha256="2" * 64,
                                      expected_fingerprint="0" * 16,
                                      repo_root=_REPO_ROOT)

    def test_unknown_topology_fails_closed(self, identity):
        mod, path, _ = identity
        with pytest.raises(ModelIdentityError, match="unknown inference topology"):
            mod.verify_model_identity(_props(path), topology="cluster",
                                      repo_root=_REPO_ROOT)

    def test_both_topologies_accept_same_identity(self, identity):
        mod, path, sha = identity
        for topo in (LOCAL, RUNPOD):
            v = mod.verify_model_identity(_props(path), topology=topo,
                                          repo_root=_REPO_ROOT)
            assert v.topology == topo and v.gguf_sha256 == sha

    def test_alias_checked_independently_of_sha(self, identity):
        """Right bytes, wrong alias must fail: identity is alias AND content."""
        mod, path, _ = identity
        with pytest.raises(ModelIdentityError, match="alias mismatch"):
            mod.verify_model_identity(_props(path, alias="impostor"), topology=LOCAL,
                                      repo_root=_REPO_ROOT)


class TestProvenance:
    def test_provenance_records_topology_and_owner(self, identity):
        mod, path, sha = identity
        v = mod.verify_model_identity(_props(path), topology=LOCAL,
                                      endpoint_owner="llama.exe", boot_id="boot-123",
                                      repo_root=_REPO_ROOT)
        p = v.as_provenance()
        assert p["inference_topology"] == "local"
        assert p["inference_endpoint_owner"] == "llama.exe"
        assert p["router_boot_id"] == "boot-123"
        assert p["model_gguf_sha256"] == sha
        assert p["model_alias"] == EXPECTED_MODEL_ALIAS
        assert p["model_gguf_size_bytes"] > 0
        assert p["props_fingerprint"] == v.props_fingerprint


class TestPreregistration:
    def test_expected_values_are_the_authorized_ones(self):
        assert EXPECTED_MODEL_ALIAS == "robs4b"
        assert EXPECTED_GGUF_SHA256 == (
            "65202f372110dde854b40ce15dcd1b6ab56a1fe9ea542b84b6a9cc745b242d41"
        )
        assert EXPECTED_GGUF_SIZE_BYTES == 2708803840

    def test_props_fingerprint_is_deterministic(self):
        p = {"build_info": "b", "model_path": "m", "total_slots": 1,
             "default_generation_settings": {"n_ctx": 16384}}
        assert props_fingerprint(p) == props_fingerprint(dict(p))

    def test_props_fingerprint_detects_drift(self):
        a = {"build_info": "b", "model_path": "m", "total_slots": 1,
             "default_generation_settings": {"n_ctx": 16384}}
        assert props_fingerprint(a) != props_fingerprint(dict(a, total_slots=2))
        assert props_fingerprint(a) != props_fingerprint(dict(a, build_info="z"))


# ---------------------------------------------------------------------------
# _run_preflight driver
# ---------------------------------------------------------------------------


def _ev(tmp_path, monkeypatch, *, pinned, owner, props, pin_exists, harness_key="k",
        status_code=200, env_pinned=None, pin_props=None):
    """Drive the REAL BenchmarkEvaluator._run_preflight with fakes.

    ``env_pinned`` defaults to agreeing with ``pinned``. Pass ``"__absent__"`` or
    a junk string to exercise the env branches; ``"__missing__"`` for ``pinned``
    omits the field entirely.
    """
    import httpx as _httpx

    import swarm_os.services.experiment_model_identity as mod

    if props is not None:
        monkeypatch.setattr(mod, "EXPECTED_LOCAL_PROPS_FINGERPRINT",
                            props_fingerprint(props))
    if env_pinned is None:
        env_pinned = "1" if pinned is True else "0"
    if env_pinned == "__absent__":
        monkeypatch.delenv("SWARM_ROUTER_PINNED", raising=False)
    else:
        monkeypatch.setenv("SWARM_ROUTER_PINNED", env_pinned)
    monkeypatch.setenv("SWARM_HARNESS_KEY", harness_key)

    pin = tmp_path / "pin.json"
    if pin_exists:
        snapshot = pin_props if pin_props is not None else (props or {})
        pin.write_text(json.dumps({**snapshot, "tunnel_port": 8079,
                                   "status_port": 8095}), encoding="utf-8")
    monkeypatch.setattr(BenchmarkEvaluator, "_pin_config_path", staticmethod(lambda: pin))
    monkeypatch.setattr(BenchmarkEvaluator, "_props_hash", staticmethod(props_fingerprint))

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None, **kw):
            if url.endswith("/inference/status"):
                if status_code != 200:
                    return _Resp({}, status_code)
                body = {"boot_id": "boot-1", "completion_requests": 0,
                        "pairs": {}, "pairless": 0}
                if pinned != "__missing__":
                    body["pinned"] = pinned
                return _Resp(body)
            if url.endswith("/props"):
                if props is None:
                    raise RuntimeError("props unreachable")
                return _Resp(props)
            raise AssertionError(f"unexpected URL {url}")

    monkeypatch.setattr(_httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(
        "psutil.net_connections",
        lambda kind="inet": [type("C", (), {
            "laddr": type("A", (), {"port": 8079})(),
            "status": "LISTEN", "pid": 1})()],
    )
    monkeypatch.setattr("psutil.Process",
                        lambda pid: type("P", (), {"name": lambda s: owner})())

    ev = BenchmarkEvaluator.__new__(BenchmarkEvaluator)
    return asyncio.run(ev._run_preflight())


def _prepared(tmp_path, monkeypatch):
    """Synthetic GGUF + patched preregistered size/SHA. Returns (path, sha)."""
    import swarm_os.services.experiment_model_identity as mod

    path, sha, size = _fake_gguf(tmp_path)
    monkeypatch.setattr(mod, "EXPECTED_GGUF_SIZE_BYTES", size)
    monkeypatch.setattr(mod, "EXPECTED_GGUF_SHA256", sha)
    return path, sha


class TestTopologySelection:
    def test_local_passes_without_pin(self, tmp_path, monkeypatch):
        path, sha = _prepared(tmp_path, monkeypatch)
        r = _ev(tmp_path, monkeypatch, pinned=False, owner="llama.exe",
                props=_props(path), pin_exists=False)
        assert r["ok"] is True, r
        assert r["inference_topology"] == LOCAL

    def test_runpod_passes_with_pin(self, tmp_path, monkeypatch):
        path, sha = _prepared(tmp_path, monkeypatch)
        r = _ev(tmp_path, monkeypatch, pinned=True, owner="ssh.exe",
                props=_props(path), pin_exists=True)
        assert r["ok"] is True, r
        assert r["inference_topology"] == RUNPOD

    def test_runpod_requires_pin(self, tmp_path, monkeypatch):
        r = _ev(tmp_path, monkeypatch, pinned=True, owner="ssh.exe",
                props=_props(tmp_path / "x.gguf"), pin_exists=False)
        assert r["ok"] is False
        assert "requires a pin config" in r["verify_reason"]

    def test_runpod_rejects_non_ssh_owner(self, tmp_path, monkeypatch):
        path, sha = _prepared(tmp_path, monkeypatch)
        r = _ev(tmp_path, monkeypatch, pinned=True, owner="llama.exe",
                props=_props(path), pin_exists=True)
        assert r["ok"] is False
        assert "ssh.exe" in r["verify_reason"]

    def test_runpod_pin_fingerprint_mismatch_still_fails(self, tmp_path, monkeypatch):
        path, sha = _prepared(tmp_path, monkeypatch)
        r = _ev(tmp_path, monkeypatch, pinned=True, owner="ssh.exe",
                props=_props(path), pin_exists=True,
                pin_props={"build_info": "different", "model_path": "different",
                           "total_slots": 9,
                           "default_generation_settings": {"n_ctx": 4096}})
        assert r["ok"] is False
        assert "fingerprint mismatch" in r["verify_reason"]

    def test_missing_harness_auth_fails(self, tmp_path, monkeypatch):
        path, sha = _prepared(tmp_path, monkeypatch)
        r = _ev(tmp_path, monkeypatch, pinned=False, owner="llama.exe",
                props=_props(path), pin_exists=False, status_code=401)
        assert r["ok"] is False and "401" in r["verify_reason"]

    def test_props_failure_fails(self, tmp_path, monkeypatch):
        r = _ev(tmp_path, monkeypatch, pinned=False, owner="llama.exe",
                props=None, pin_exists=False)
        assert r["ok"] is False and "/props unreachable" in r["verify_reason"]

    def test_local_wrong_model_fails_closed(self, tmp_path, monkeypatch):
        """Topology is not a bypass for identity."""
        path, sha = _prepared(tmp_path, monkeypatch)
        r = _ev(tmp_path, monkeypatch, pinned=False, owner="llama.exe",
                props=_props(path, alias="wrong-model"), pin_exists=False)
        assert r["ok"] is False and "model identity" in r["verify_reason"]

    def test_provenance_present_on_success(self, tmp_path, monkeypatch):
        path, sha = _prepared(tmp_path, monkeypatch)
        r = _ev(tmp_path, monkeypatch, pinned=False, owner="llama.exe",
                props=_props(path), pin_exists=False)
        mi = r["model_identity"]
        assert mi["inference_topology"] == LOCAL
        assert mi["inference_endpoint_owner"] == "llama.exe"
        assert mi["model_gguf_sha256"] == sha
        assert mi["router_boot_id"] == "boot-1"


class TestTopologyStrictness:
    def test_missing_pinned_fails_closed(self, tmp_path, monkeypatch):
        r = _ev(tmp_path, monkeypatch, pinned="__missing__", owner="llama.exe",
                props=_props(tmp_path / "x.gguf"), pin_exists=False,
                env_pinned="__absent__")
        assert r["ok"] is False and "pinned" in r["verify_reason"]

    def test_pinned_string_false_fails_closed(self, tmp_path, monkeypatch):
        r = _ev(tmp_path, monkeypatch, pinned="false", owner="llama.exe",
                props=_props(tmp_path / "x.gguf"), pin_exists=False)
        assert r["ok"] is False
        assert "usable 'pinned' boolean" in r["verify_reason"]

    def test_pinned_int_zero_fails_closed(self, tmp_path, monkeypatch):
        r = _ev(tmp_path, monkeypatch, pinned=0, owner="llama.exe",
                props=_props(tmp_path / "x.gguf"), pin_exists=False)
        assert r["ok"] is False
        assert "usable 'pinned' boolean" in r["verify_reason"]

    def test_env_runpod_but_status_local_fails_closed(self, tmp_path, monkeypatch):
        """Must not silently downgrade RunPod to Local."""
        r = _ev(tmp_path, monkeypatch, pinned=False, owner="llama.exe",
                props=_props(tmp_path / "x.gguf"), pin_exists=False,
                env_pinned="1")
        assert r["ok"] is False and "disagree" in r["verify_reason"]

    def test_env_local_but_status_pinned_fails_closed(self, tmp_path, monkeypatch):
        r = _ev(tmp_path, monkeypatch, pinned=True, owner="ssh.exe",
                props=_props(tmp_path / "x.gguf"), pin_exists=False,
                env_pinned="0")
        assert r["ok"] is False and "disagree" in r["verify_reason"]

    def test_malformed_env_fails_closed(self, tmp_path, monkeypatch):
        r = _ev(tmp_path, monkeypatch, pinned=False, owner="llama.exe",
                props=_props(tmp_path / "x.gguf"), pin_exists=False,
                env_pinned="yes-please")
        assert r["ok"] is False
        assert "malformed SWARM_ROUTER_PINNED" in r["verify_reason"]

    def test_absent_env_defaults_local(self, tmp_path, monkeypatch):
        """Documented local default (model_router.py:98) is allowed."""
        path, sha = _prepared(tmp_path, monkeypatch)
        r = _ev(tmp_path, monkeypatch, pinned=False, owner="llama.exe",
                props=_props(path), pin_exists=False, env_pinned="__absent__")
        assert r["ok"] is True, r
        assert r["inference_topology"] == LOCAL

    def test_local_rejects_non_llama_owner(self, tmp_path, monkeypatch):
        path, sha = _prepared(tmp_path, monkeypatch)
        r = _ev(tmp_path, monkeypatch, pinned=False, owner="python.exe",
                props=_props(path), pin_exists=False)
        assert r["ok"] is False and "local llama" in r["verify_reason"]

    def test_local_rejects_ssh_owner(self, tmp_path, monkeypatch):
        path, sha = _prepared(tmp_path, monkeypatch)
        r = _ev(tmp_path, monkeypatch, pinned=False, owner="ssh.exe",
                props=_props(path), pin_exists=False)
        assert r["ok"] is False and "local llama" in r["verify_reason"]

    def test_pinned_true_local_owner_cannot_masquerade(self, tmp_path, monkeypatch):
        path, sha = _prepared(tmp_path, monkeypatch)
        r = _ev(tmp_path, monkeypatch, pinned=True, owner="llama.exe",
                props=_props(path), pin_exists=True)
        assert r["ok"] is False and "ssh.exe" in r["verify_reason"]


# ---------------------------------------------------------------------------
# Postcheck
# ---------------------------------------------------------------------------


def _postcheck(tmp_path, monkeypatch, topology, pin_exists):
    import httpx as _httpx

    pin = tmp_path / "pin.json"
    if pin_exists:
        pin.write_text(json.dumps({"tunnel_port": 8079, "status_port": 8095}),
                       encoding="utf-8")
    monkeypatch.setenv("SWARM_HARNESS_KEY", "k")
    monkeypatch.setattr(BenchmarkEvaluator, "_pin_config_path", staticmethod(lambda: pin))
    monkeypatch.setattr(BenchmarkEvaluator, "_props_hash", staticmethod(props_fingerprint))

    fp = props_fingerprint({"build_info": "b", "model_path": "m", "total_slots": 1,
                            "default_generation_settings": {"n_ctx": 1}})

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None, **kw):
            if url.endswith("/inference/status"):
                return _Resp({"pinned": topology == RUNPOD, "boot_id": "boot-1",
                              "completion_requests": 1, "pairs": {"single": 1},
                              "pairless": 0})
            if url.endswith("/props"):
                return _Resp({"build_info": "b", "model_path": "m", "total_slots": 1,
                              "default_generation_settings": {"n_ctx": 1}})
            raise AssertionError(url)

    monkeypatch.setattr(_httpx, "AsyncClient", FakeClient)
    ev = BenchmarkEvaluator.__new__(BenchmarkEvaluator)
    return asyncio.run(ev._run_postcheck({
        "boot_id": "boot-1", "inference_endpoint": fp,
        "inference_topology": topology,
        "pre_counters": {"completion_requests": 0, "pairs": {}, "pairless": 0},
    }))


class TestPostcheckTopology:
    def test_local_needs_no_pin(self, tmp_path, monkeypatch):
        r = _postcheck(tmp_path, monkeypatch, LOCAL, pin_exists=False)
        assert r["ok"] is True, r

    def test_runpod_requires_pin(self, tmp_path, monkeypatch):
        r = _postcheck(tmp_path, monkeypatch, RUNPOD, pin_exists=False)
        assert r["ok"] is False
        assert "requires a pin config" in r["verify_reason"]

    def test_runpod_with_pin(self, tmp_path, monkeypatch):
        r = _postcheck(tmp_path, monkeypatch, RUNPOD, pin_exists=True)
        assert r["ok"] is True, r

    def test_boot_id_drift_fails(self, tmp_path, monkeypatch):
        """Existing safeguard preserved: endpoint restart mid-arm fails."""
        r = _postcheck(tmp_path, monkeypatch, LOCAL, pin_exists=False)
        assert r["ok"] is True  # baseline
        # now with a mismatched preflight boot_id
        import httpx as _httpx

        class FakeClient:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, headers=None, **kw):
                if url.endswith("/inference/status"):
                    return _Resp({"pinned": False, "boot_id": "RESTARTED",
                                  "completion_requests": 1,
                                  "pairs": {"single": 1}, "pairless": 0})
                return _Resp({"build_info": "b", "model_path": "m", "total_slots": 1,
                              "default_generation_settings": {"n_ctx": 1}})

        monkeypatch.setattr(_httpx, "AsyncClient", FakeClient)
        ev = BenchmarkEvaluator.__new__(BenchmarkEvaluator)
        fp = props_fingerprint({"build_info": "b", "model_path": "m", "total_slots": 1,
                                "default_generation_settings": {"n_ctx": 1}})
        r2 = asyncio.run(ev._run_postcheck({
            "boot_id": "boot-1", "inference_endpoint": fp,
            "inference_topology": LOCAL,
            "pre_counters": {"completion_requests": 0, "pairs": {}, "pairless": 0},
        }))
        assert r2["ok"] is False
        assert "boot_id" in r2["verify_reason"]


# ---------------------------------------------------------------------------
# Provenance persistence (F0 :144/:145 REQUIRED RECORD)
# ---------------------------------------------------------------------------


class TestProvenancePersists:
    def test_rollout_evidence_merges_model_identity(self):
        """The verified identity must reach `_append_rollout`, not just preflight."""
        import inspect

        from swarm_os.services.prompt_repairer import BenchmarkEvaluator as BE

        src = inspect.getsource(BE._run_swe_harness)
        assert '**(preflight.get("model_identity") or {})' in src

    def test_identity_fields_survive_serialisation(self):
        identity = {
            "model_alias": "robs4b",
            "model_gguf_sha256": "65202f37" * 8,
            "model_gguf_size_bytes": 2708803840,
            "props_fingerprint": "23408a87ffe0f2cb",
            "inference_topology": "local",
            "inference_endpoint_owner": "llama.exe",
            "router_boot_id": "boot-1",
        }
        preflight = {"inference_endpoint": identity["props_fingerprint"],
                     "boot_id": "boot-1", "model_identity": identity}
        record = {"task_id": "t", "arm": "candidate",
                  "inference_endpoint": preflight["inference_endpoint"],
                  "router_boot_id": preflight["boot_id"],
                  **(preflight.get("model_identity") or {})}
        back = json.loads(json.dumps(record))
        for field in ("model_alias", "model_gguf_sha256", "model_gguf_size_bytes",
                      "props_fingerprint", "inference_topology",
                      "inference_endpoint_owner", "router_boot_id"):
            assert field in back, f"{field} did not survive serialisation"
        # existing keys preserved
        assert back["inference_endpoint"] == identity["props_fingerprint"]
        assert back["task_id"] == "t"