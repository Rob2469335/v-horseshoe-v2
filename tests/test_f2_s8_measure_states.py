"""``f2-s8-state-derivation/v1`` - measured base/gold state derivation tests.

Covers side-path resolution for both builder layouts, the derivation itself,
fail-closed behaviour on every missing or inconsistent input, determinism, and
the base/gold independence rule.  Nothing here contacts a guest, mutates
``C:\\f2_pilot``, runs a task's declared test command or consumes any
authorization.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from qwen_train import f2_s8_measure_states as ms
from qwen_train.f2_execution_state import (
    SIDE_BASE,
    SIDE_GOLD,
    verify_execution_state,
)


def _write_task_workspace(ws: Path, *, uses_uv: bool) -> Path:
    """A miniature but REAL workspace in one of the two builder layouts."""
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "repo").mkdir(exist_ok=True)
    (ws / "repo" / "mod.py").write_text("X = 1\n", encoding="utf-8")
    (ws / "repo_gold").mkdir(exist_ok=True)
    (ws / "repo_gold" / "mod.py").write_text("X = 1\n# gold patch applied\n", encoding="utf-8")
    if uses_uv:
        for tree in ("repo", "repo_gold"):
            (ws / tree / ".venv" / "Scripts").mkdir(parents=True, exist_ok=True)
    else:
        for name in (".venv", ".venv_gold"):
            (ws / name / "Scripts").mkdir(parents=True, exist_ok=True)
    return ws


def _patches(tmp_path: Path) -> dict:
    test_patch = tmp_path / "patches" / "t.patch"
    gold_patch = tmp_path / "patches" / "g.patch"
    test_patch.parent.mkdir(parents=True, exist_ok=True)
    test_patch.write_text("--- a\n+++ b\n", encoding="utf-8")
    gold_patch.write_text("--- a\n+++ b\n+# fix\n", encoding="utf-8")
    return {
        "task_id": "acme__repo-1",
        "repo": "acme/repo",
        "base_commit": "c" * 40,
        "test_cmd": "pytest -rA",
        "test_patch": str(test_patch),
        "gold_patch": str(gold_patch),
    }


def _entry(ws: Path, *, uses_uv: bool = False, status: str = "built") -> dict:
    return {
        "task_id": "acme__repo-1",
        "status": status,
        "workspace": str(ws),
        "uses_uv": uses_uv,
        "base_commit": "c" * 40,
    }


_FAKE_ENV = {
    "interpreter": "cpython-3.14.8:C:/venv/Scripts/python.exe",
    "distributions_digest": "d" * 64,
    "distribution_count": 7,
    "version": "3.14.8",
}


# --------------------------------------------------------------------------
# side-path resolution
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "uses_uv,base_tree,gold_tree,base_venv,gold_venv",
    [
        (False, "repo", "repo_gold", ".venv", ".venv_gold"),
        (True, "repo", "repo_gold", "repo/.venv", "repo_gold/.venv"),
    ],
)
def test_resolve_side_paths_covers_both_builder_layouts(
    tmp_path: Path, uses_uv: bool, base_tree: str, gold_tree: str,
    base_venv: str, gold_venv: str,
) -> None:
    ws = _write_task_workspace(tmp_path / "ws", uses_uv=uses_uv)
    entry = _entry(ws, uses_uv=uses_uv)

    b_tree, b_venv = ms.resolve_side_paths(entry, SIDE_BASE)
    g_tree, g_venv = ms.resolve_side_paths(entry, SIDE_GOLD)

    assert b_tree == ws / base_tree
    assert g_tree == ws / gold_tree
    assert b_venv == ws / Path(base_venv)
    assert g_venv == ws / Path(gold_venv)
    # the two sides must never resolve to the same tree
    assert b_tree != g_tree
    assert b_venv != g_venv


def test_resolve_side_paths_rejects_unknown_side(tmp_path: Path) -> None:
    entry = _entry(tmp_path / "ws")
    with pytest.raises(ms.DerivationError, match="unknown side"):
        ms.resolve_side_paths(entry, "shiny")


# --------------------------------------------------------------------------
# derivation
# --------------------------------------------------------------------------
def test_derive_task_states_produces_verified_distinct_pair(
    tmp_path: Path, monkeypatch
) -> None:
    ws = _write_task_workspace(tmp_path / "ws", uses_uv=False)
    monkeypatch.setattr(ms, "measure_venv_environment", lambda _v: dict(_FAKE_ENV))

    result = ms.derive_task_states(
        "acme__repo-1", _entry(ws), _patches(tmp_path), environment_label="test-env"
    )

    assert result["pair_state"] == "ES_VERIFIED"
    assert result["states_distinct"] is True
    assert result["base_state_digest"] != result["gold_state_digest"]
    # base carries no gold patch digest; gold does
    assert result["base"]["gold_patch_digest"] == ""
    assert len(result["gold"]["gold_patch_digest"]) == 64
    # identity bindings survive verification
    verdict = verify_execution_state(
        result["base"],
        expected_instance_id="acme__repo-1",
        expected_repository="acme/repo",
        expected_base_commit="c" * 40,
        expected_side=SIDE_BASE,
        expected_environment_identity=result["base"]["environment_identity"],
    )
    assert verdict.ok


def test_derivation_is_deterministic(tmp_path: Path, monkeypatch) -> None:
    ws = _write_task_workspace(tmp_path / "ws", uses_uv=False)
    monkeypatch.setattr(ms, "measure_venv_environment", lambda _v: dict(_FAKE_ENV))
    patch = _patches(tmp_path)
    a = ms.derive_task_states("acme__repo-1", _entry(ws), patch, environment_label="e")
    b = ms.derive_task_states("acme__repo-1", _entry(ws), patch, environment_label="e")
    assert a["base_state_digest"] == b["base_state_digest"]
    assert a["gold_state_digest"] == b["gold_state_digest"]


def test_environment_identity_excludes_the_venv_path(tmp_path: Path, monkeypatch) -> None:
    """Base and gold run in different venvs but the SAME environment identity.

    The per-side path difference is already carried by ``interpreter``; folding
    it into ``environment_identity`` would make the pair unverifiable, because
    the contract requires both sides to describe one environment.
    """
    ws = _write_task_workspace(tmp_path / "ws", uses_uv=False)

    def _env_for(venv: str) -> dict:
        return {
            **_FAKE_ENV,
            "interpreter": f"cpython-3.14.8:{venv.replace(chr(92), '/')}/Scripts/python.exe",
        }

    monkeypatch.setattr(ms, "measure_venv_environment", lambda v: _env_for(str(v)))
    result = ms.derive_task_states(
        "acme__repo-1", _entry(ws), _patches(tmp_path), environment_label="env"
    )
    assert result["base"]["environment_identity"] == result["gold"]["environment_identity"]
    assert result["base"]["interpreter"] != result["gold"]["interpreter"]


def test_changing_the_gold_tree_changes_only_the_gold_state(tmp_path: Path, monkeypatch) -> None:
    ws = _write_task_workspace(tmp_path / "ws", uses_uv=False)
    monkeypatch.setattr(ms, "measure_venv_environment", lambda _v: dict(_FAKE_ENV))
    patch = _patches(tmp_path)
    before = ms.derive_task_states("acme__repo-1", _entry(ws), patch, environment_label="e")

    (ws / "repo_gold" / "mod.py").write_text("X = 1\n# DIFFERENT fix\n", encoding="utf-8")
    after = ms.derive_task_states("acme__repo-1", _entry(ws), patch, environment_label="e")

    # the base state is untouched by a gold-side edit; the gold state changes
    assert before["base_state_digest"] == after["base_state_digest"]
    assert before["gold_state_digest"] != after["gold_state_digest"]


# --------------------------------------------------------------------------
# fail closed
# --------------------------------------------------------------------------
def test_rejects_an_entry_that_was_not_built(tmp_path: Path, monkeypatch) -> None:
    ws = _write_task_workspace(tmp_path / "ws", uses_uv=False)
    monkeypatch.setattr(ms, "measure_venv_environment", lambda _v: dict(_FAKE_ENV))
    with pytest.raises(ms.DerivationError, match="not a built entry"):
        ms.derive_task_states(
            "acme__repo-1", _entry(ws, status="build_failure"), _patches(tmp_path),
            environment_label="e",
        )


def test_rejects_a_missing_tree(tmp_path: Path, monkeypatch) -> None:
    ws = _write_task_workspace(tmp_path / "ws", uses_uv=False)
    monkeypatch.setattr(ms, "measure_venv_environment", lambda _v: dict(_FAKE_ENV))
    import shutil

    shutil.rmtree(ws / "repo_gold")
    with pytest.raises(ms.DerivationError, match="tree missing"):
        ms.derive_task_states(
            "acme__repo-1", _entry(ws), _patches(tmp_path), environment_label="e"
        )


def test_rejects_missing_patches_and_test_command(tmp_path: Path, monkeypatch) -> None:
    ws = _write_task_workspace(tmp_path / "ws", uses_uv=False)
    monkeypatch.setattr(ms, "measure_venv_environment", lambda _v: dict(_FAKE_ENV))
    patch = _patches(tmp_path)

    no_cmd = dict(patch, test_cmd="")
    with pytest.raises(ms.DerivationError, match="no declared test_cmd"):
        ms.derive_task_states("acme__repo-1", _entry(ws), no_cmd, environment_label="e")

    no_patch = dict(patch, gold_patch=str(tmp_path / "nope.patch"))
    with pytest.raises(ms.DerivationError, match="gold_patch missing on disk"):
        ms.derive_task_states("acme__repo-1", _entry(ws), no_patch, environment_label="e")


def test_rejects_an_environment_that_cannot_be_measured(tmp_path: Path, monkeypatch) -> None:
    """A venv that cannot answer is a hard failure, never a guess."""
    ws = _write_task_workspace(tmp_path / "ws", uses_uv=False)
    monkeypatch.setattr(
        ms, "measure_venv_environment",
        lambda _v: (_ for _ in ()).throw(ms.DerivationError("venv interpreter missing")),
    )
    with pytest.raises(ms.DerivationError, match="venv interpreter missing"):
        ms.derive_task_states("acme__repo-1", _entry(ws), _patches(tmp_path), environment_label="e")


def test_measure_venv_environment_refuses_a_missing_interpreter(tmp_path: Path) -> None:
    with pytest.raises(ms.DerivationError, match="interpreter missing"):
        ms.measure_venv_environment(tmp_path / "not-a-venv")


# --------------------------------------------------------------------------
# real venv probe (offline, ensurepip's bundled wheel)
# --------------------------------------------------------------------------
def test_measure_venv_environment_accepts_a_well_formed_probe(tmp_path: Path, monkeypatch) -> None:
    """Exercise the probe round-trip WITHOUT spawning.

    ``tests/conftest.py`` installs an autouse ``global_subprocess_mock`` that
    blocks every real subprocess in this suite (a deliberate repo-wide guard, not
    weakened here).  So the probe protocol is exercised by driving
    ``subprocess.run`` directly: a well-formed answer is accepted, and every
    malformed one fails closed.
    """
    import types

    target = tmp_path / "venv"
    (target / "Scripts").mkdir(parents=True)
    python = target / "Scripts" / "python.exe"
    python.write_bytes(b"MZ")

    def _fake(stdout: bytes, rc: int = 0, stderr: bytes = b""):
        return types.SimpleNamespace(returncode=rc, stdout=stdout, stderr=stderr)

    good = json.dumps({
        "interpreter": "cpython-3.14.8:C:/x/Scripts/python.exe",
        "distributions_digest": "e" * 64,
        "distribution_count": 3,
        "version": "3.14.8",
    }).encode()

    captured: dict[str, object] = {}

    def _run(cmd, **kw):
        captured["cmd"] = cmd
        return _fake(good)

    monkeypatch.setattr(ms.subprocess, "run", _run)
    env = ms.measure_venv_environment(target)
    assert env["interpreter"].startswith("cpython-")
    assert env["distribution_count"] == 3
    # the probe must disable byte-code writes and user site, and probe the venv's
    # OWN interpreter -- measuring a workspace may never mutate it
    assert captured["cmd"][0] == str(python)
    assert "-B" in captured["cmd"] and "-I" in captured["cmd"]
    assert ms._MEASURE_ENV["PYTHONDONTWRITEBYTECODE"] == "1"
    assert ms._MEASURE_ENV["PYTHONNOUSERSITE"] == "1"


def test_measure_venv_environment_fails_closed_on_bad_answers(tmp_path: Path, monkeypatch) -> None:
    import types

    (tmp_path / "Scripts").mkdir(parents=True)
    (tmp_path / "Scripts" / "python.exe").write_bytes(b"MZ")

    def _with(stdout=b"", rc=0, stderr=b""):
        monkeypatch.setattr(
            ms.subprocess, "run",
            lambda *a, **k: types.SimpleNamespace(returncode=rc, stdout=stdout, stderr=stderr),
        )

    _with(rc=3, stderr=b"No module named site")
    with pytest.raises(ms.DerivationError, match="venv probe failed rc=3"):
        ms.measure_venv_environment(tmp_path)

    _with(stdout=b"not json at all\n")
    with pytest.raises(ms.DerivationError, match="unparseable"):
        ms.measure_venv_environment(tmp_path)

    _with(stdout=b'{"interpreter": ""}\n')
    with pytest.raises(ms.DerivationError, match="omitted interpreter"):
        ms.measure_venv_environment(tmp_path)


# --------------------------------------------------------------------------
# manifest-level derivation
# --------------------------------------------------------------------------
def test_derive_manifest_states_selects_built_and_records_failures(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(ms, "measure_venv_environment", lambda _v: dict(_FAKE_ENV))
    ws_ok = _write_task_workspace(tmp_path / "ok", uses_uv=False)
    ws_bad = _write_task_workspace(tmp_path / "bad", uses_uv=False)

    build = tmp_path / "build_manifest.json"
    build.write_text(json.dumps({
        "acme__repo-1": _entry(ws_ok),
        "acme__repo-2": {"status": "build_failure", "detail": "boom"},
        "acme__repo-3": _entry(ws_bad),
    }), encoding="utf-8")

    patches = tmp_path / "phase2_patches.json"
    patches.write_text(json.dumps({"tasks": [
        _patches(tmp_path),
        dict(_patches(tmp_path), task_id="acme__repo-3"),
    ]}), encoding="utf-8")

    doc = ms.derive_manifest_states(
        build_manifest=build, patches_manifest=patches, environment_label="e"
    )
    assert doc["derived_count"] == 2
    assert doc["failed_count"] == 0
    assert {r["task_id"] for r in doc["results"]} == {"acme__repo-1", "acme__repo-3"}
    # build_failure entries are not derived and not silently dropped
    assert doc["candidate_manifest_digest"] and len(doc["candidate_manifest_digest"]) == 64
    for result in doc["results"]:
        assert result["pair_state"] == "ES_VERIFIED"


def test_derive_manifest_states_records_a_task_missing_from_the_patches(
    tmp_path: Path, monkeypatch
) -> None:
    """A built task with no patches entry is a recorded failure, not a crash.

    The patches manifest must be non-empty (an empty one is a broken input and
    fails closed for the whole run); a *gap* inside a populated manifest is
    reported per task.
    """
    monkeypatch.setattr(ms, "measure_venv_environment", lambda _v: dict(_FAKE_ENV))
    ws = _write_task_workspace(tmp_path / "ws", uses_uv=False)
    build = tmp_path / "build_manifest.json"
    build.write_text(json.dumps({"acme__repo-1": _entry(ws)}), encoding="utf-8")

    other = _patches(tmp_path)
    other["task_id"] = "acme__other-9"
    patches = tmp_path / "p.json"
    patches.write_text(json.dumps({"tasks": [other]}), encoding="utf-8")

    doc = ms.derive_manifest_states(
        build_manifest=build, patches_manifest=patches, environment_label="e"
    )
    assert doc["derived_count"] == 0
    assert doc["failed_count"] == 1
    assert "no entry in patches manifest" in doc["failures"][0]["detail"]


def test_derive_manifest_states_refuses_an_empty_patches_manifest(tmp_path: Path) -> None:
    """Zero patch entries is a configuration error, so it fails closed outright."""
    ws = _write_task_workspace(tmp_path / "ws", uses_uv=False)
    build = tmp_path / "build_manifest.json"
    build.write_text(json.dumps({"acme__repo-1": _entry(ws)}), encoding="utf-8")
    patches = tmp_path / "p.json"
    patches.write_text(json.dumps({"tasks": []}), encoding="utf-8")

    with pytest.raises(ms.DerivationError, match="no task entries"):
        ms.derive_manifest_states(
            build_manifest=build, patches_manifest=patches, environment_label="e"
        )


def test_cli_writes_the_derivation_artifact(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(ms, "measure_venv_environment", lambda _v: dict(_FAKE_ENV))
    ws = _write_task_workspace(tmp_path / "ws", uses_uv=False)
    build = tmp_path / "build_manifest.json"
    build.write_text(json.dumps({"acme__repo-1": _entry(ws)}), encoding="utf-8")
    patches = tmp_path / "p.json"
    patches.write_text(json.dumps({"tasks": [_patches(tmp_path)]}), encoding="utf-8")
    out = tmp_path / "states.json"

    rc = ms.main([
        "--build-manifest", str(build), "--patches-manifest", str(patches),
        "--out", str(out), "--quiet",
    ])
    assert rc == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["derived_count"] == 1 and doc["failed_count"] == 0
    assert doc["schema_version"] == ms.SCHEMA_VERSION
    capsys.readouterr()
