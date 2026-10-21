"""Scratch-only regression tests for the RATIFIED-sidecar interpreter patch (v3).

Not part of the tracked suite.  Every case builds throw-away fixtures; the real
sidecar is exercised once at the end.
"""
from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

import pytest

from qwen_train import f2_s8_pilot_build as pb

ALEXA = "alandtse__alexa_media_player-2973"
TASK = "pybamm-team__PyBaMM-5122"


# ------------------------------------------------------------------ fixtures
def sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def write_json(path: Path, obj) -> Path:
    path.write_text(json.dumps(obj, indent=1), encoding="utf-8", newline="\n")
    return path


def ratified(minor: str = "3.10") -> dict:
    return {"status": "ratified", "ratified": True, "ratified_by": "Rob",
            "ratified_at": "2026-10-20", "declared_python_minor": minor,
            "requires_python_compatibility": "OK", "evidence_basis": "SUPPORTED"}


def blocked() -> dict:
    return {"status": "blocked_conflict", "ratified": False,
            "declared_python_minor": None, "evidence_basis": "NOT ESTABLISHED"}


def bundle(root: Path, decls: dict, *, sidecar_schema="f2-s8-declared-environments/v2",
           prov_schema="f2-s8-declared-environments-provenance/v2",
           ratified_by="Rob", sidecar_sha=None, old_sha=None, pol_sha=None,
           old_schema="f2-s8-declared-environments/v1", pol_schema="f2-s8-live-image-map/v1",
           sidecar_raw=None, prov_path_override=None):
    root.mkdir(parents=True, exist_ok=True)
    old = write_json(root / "old_sidecar.json", {"schema": old_schema, "declarations": {}})
    pol = write_json(root / "policy_evidence.json", {"schema": pol_schema, "tasks": []})
    if sidecar_raw is not None:
        side = root / "sidecar.json"
        side.write_text(sidecar_raw, encoding="utf-8", newline="\n")
    else:
        side = write_json(root / "sidecar.json",
                          {"schema": sidecar_schema, "ratified_by": ratified_by,
                           "declarations": decls})
    prov = write_json(root / "prov.json", {
        "schema": prov_schema,
        "ratified_by": ratified_by,
        "ratified_at": "2026-10-20",
        "sidecar": {"path": prov_path_override or str(side),
                    "sha256": sidecar_sha if sidecar_sha is not None else sha(side),
                    "schema": sidecar_schema},
        "source_old_sidecar": {"path": str(old),
                               "sha256": old_sha if old_sha is not None else sha(old),
                               "schema": old_schema},
        "policy_evidence": {"path": str(pol),
                            "sha256": pol_sha if pol_sha is not None else sha(pol),
                            "schema": pol_schema},
    })
    return side, prov


def make_inputs(root: Path, task: str, rec: dict | None = None) -> Path:
    d = root / "inputs"
    d.mkdir(exist_ok=True)
    (d / f"{task}.json").write_text(json.dumps(rec or {"base_commit": "deadbeef"}),
                                    encoding="utf-8", newline="\n")
    return d


def project(root: Path, requires_python: str = ">=3.9") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "x"\nversion = "0"\nrequires-python = "{requires_python}"\n',
        encoding="utf-8")
    return root


# ------------------------------------------------------------------- tests
def test_all_ratified_declarations_are_accepted(tmp_path):
    decls = {f"task__x-{i}": ratified() for i in range(10)}
    side, prov = bundle(tmp_path, decls)
    got = pb.load_declared_environments(side, prov)
    assert len(got) == 10 and all(v == "3.10" for v in got.values())


def test_proposed_declaration_is_rejected(tmp_path):
    rec = {**ratified(), "status": "proposed"}
    side, prov = bundle(tmp_path, {TASK: rec})
    with pytest.raises(pb.DeclaredEnvironmentError, match="not ratified"):
        pb.load_declared_environments(side, prov)


@pytest.mark.parametrize("bad_status", [None, "maybe", "RATIFIED", ""])
def test_missing_and_unknown_status_are_rejected(tmp_path, bad_status):
    rec = ratified()
    if bad_status is None:
        rec.pop("status")
    else:
        rec["status"] = bad_status
    side, prov = bundle(tmp_path, {TASK: rec})
    with pytest.raises(pb.DeclaredEnvironmentError):
        pb.load_declared_environments(side, prov)


def test_alexa_blocked_conflict_is_not_runnable(tmp_path):
    side, prov = bundle(tmp_path, {ALEXA: blocked(), TASK: ratified()})
    loaded = pb.load_declared_environments(side, prov)
    assert ALEXA not in loaded and TASK in loaded
    inputs = make_inputs(tmp_path, ALEXA)
    assert pb._declared_minor_for(ALEXA, inputs, sidecar=side, provenance=prov) is None
    with pytest.raises(pb.DeclaredInterpreterError):
        pb.resolve_declared_interpreter(project(tmp_path), None)


def test_changed_sidecar_fails_integrity(tmp_path):
    side, prov = bundle(tmp_path, {TASK: ratified("3.10")})
    write_json(side, {"schema": "f2-s8-declared-environments/v2", "ratified_by": "Rob",
                      "declarations": {TASK: ratified("3.11")}})
    with pytest.raises(pb.DeclaredEnvironmentError, match="sidecar digest mismatch"):
        pb.load_declared_environments(side, prov)


def test_changed_policy_evidence_fails_integrity(tmp_path):
    side, prov = bundle(tmp_path, {TASK: ratified()})
    pol = json.loads(Path(prov).read_text())["policy_evidence"]["path"]
    Path(pol).write_text('{"schema":"f2-s8-live-image-map/v1","tasks":[1]}', encoding="utf-8")
    with pytest.raises(pb.DeclaredEnvironmentError, match="policy evidence digest mismatch"):
        pb.load_declared_environments(side, prov)


def test_changed_old_sidecar_fails_integrity(tmp_path):
    side, prov = bundle(tmp_path, {TASK: ratified()})
    old = json.loads(Path(prov).read_text())["source_old_sidecar"]["path"]
    Path(old).write_text('{"schema":"f2-s8-declared-environments/v1","declarations":{"x":1}}', encoding="utf-8")
    with pytest.raises(pb.DeclaredEnvironmentError, match="source old sidecar digest mismatch"):
        pb.load_declared_environments(side, prov)


def test_missing_provenance_is_rejected(tmp_path):
    side, _ = bundle(tmp_path, {TASK: ratified()})
    with pytest.raises(pb.DeclaredEnvironmentError, match="provenance missing"):
        pb.load_declared_environments(side, tmp_path / "nope.json")


def test_malformed_provenance_is_rejected(tmp_path):
    side, prov = bundle(tmp_path, {TASK: ratified()})
    Path(prov).write_text('{"schema": ', encoding="utf-8")
    with pytest.raises(pb.DeclaredEnvironmentError, match="malformed JSON"):
        pb.load_declared_environments(side, prov)


def test_wrong_provenance_schema_is_rejected(tmp_path):
    side, prov = bundle(tmp_path, {TASK: ratified()}, prov_schema="bogus/v9")
    with pytest.raises(pb.DeclaredEnvironmentError, match="provenance schema"):
        pb.load_declared_environments(side, prov)


def test_unapproved_sidecar_path_is_rejected(tmp_path):
    other = write_json(tmp_path / "other.json",
                       {"schema": "f2-s8-declared-environments/v2", "ratified_by": "Rob",
                        "declarations": {TASK: ratified("3.11")}})
    side, prov = bundle(tmp_path, {TASK: ratified("3.10")})
    with pytest.raises(pb.DeclaredEnvironmentError, match="different sidecar"):
        pb.load_declared_environments(other, prov)


def test_duplicate_task_entries_are_rejected(tmp_path):
    raw = ('{"schema":"f2-s8-declared-environments/v2","ratified_by":"Rob","declarations":{'
           f'"{TASK}":{{"status":"ratified","ratified_by":"Rob","declared_python_minor":"3.10","requires_python_compatibility":"OK"}},'
           f'"{TASK}":{{"status":"ratified","ratified_by":"Rob","declared_python_minor":"3.11","requires_python_compatibility":"OK"}}'
           '}}')
    side, prov = bundle(tmp_path, {}, sidecar_raw=raw)
    with pytest.raises(pb.DeclaredEnvironmentError, match="duplicate key"):
        pb.load_declared_environments(side, prov)


def test_record_ratified_by_another_party_is_rejected(tmp_path):
    rec = {**ratified(), "ratified_by": "someone"}
    side, prov = bundle(tmp_path, {TASK: rec})
    with pytest.raises(pb.DeclaredEnvironmentError, match="not ratified by the authorised owner"):
        pb.load_declared_environments(side, prov)


def test_sidecar_ratified_by_another_party_is_rejected(tmp_path):
    side, prov = bundle(tmp_path, {TASK: ratified()}, ratified_by="someone")
    with pytest.raises(pb.DeclaredEnvironmentError, match="ratifying authority"):
        pb.load_declared_environments(side, prov)


def test_input_and_sidecar_conflict_is_rejected(tmp_path):
    side, prov = bundle(tmp_path, {TASK: ratified("3.11")})
    inputs = make_inputs(tmp_path, TASK, {"base_commit": "d",
                                          "install_config": {"base_image_name": "python_base_310"}})
    with pytest.raises(pb.DeclaredEnvironmentError, match="input declares"):
        pb._declared_minor_for(TASK, inputs, sidecar=side, provenance=prov)


def test_invalid_minor_is_rejected(tmp_path):
    side, prov = bundle(tmp_path, {TASK: {**ratified(), "declared_python_minor": "banana"}})
    with pytest.raises(pb.DeclaredEnvironmentError, match="malformed declared_python_minor"):
        pb.load_declared_environments(side, prov)


def test_non_ok_compatibility_is_rejected(tmp_path):
    side, prov = bundle(tmp_path, {TASK: {**ratified(), "requires_python_compatibility": "CONFLICT"}})
    with pytest.raises(pb.DeclaredEnvironmentError, match="not OK"):
        pb.load_declared_environments(side, prov)


def test_declared_minor_violating_requires_python_fails_closed(tmp_path):
    with pytest.raises(pb.DeclaredInterpreterError):
        pb.resolve_declared_interpreter(project(tmp_path, ">=3.11,<4.0"), "3.10")


def test_no_fallback_to_an_arbitrary_default(tmp_path):
    inputs = make_inputs(tmp_path, TASK)
    with pytest.raises(pb.DeclaredEnvironmentError, match="sidecar missing"):
        pb._declared_minor_for(TASK, inputs, sidecar=tmp_path / "absent.json",
                               provenance=tmp_path / "absent_prov.json")
    with pytest.raises(pb.DeclaredInterpreterError):
        pb.resolve_declared_interpreter(project(tmp_path), None)


def test_valid_record_for_a_different_task_is_not_returned(tmp_path):
    side, prov = bundle(tmp_path, {"another__task-1": ratified()})
    inputs = make_inputs(tmp_path, TASK)
    assert pb._declared_minor_for(TASK, inputs, sidecar=side, provenance=prov) is None


def test_declared_minor_beats_the_312_default(tmp_path):
    p312 = tmp_path / "py312.exe"
    p312.write_bytes(b"")
    p310 = tmp_path / "py310.exe"
    p310.write_bytes(b"")
    mp = pytest.MonkeyPatch()
    mp.setattr(pb, "available_interpreters",
               lambda prefer=None: [str(p312), str(p310)] if prefer is None else [str(p310)])
    mp.setattr(pb, "_version_of", lambda p: (3, 12) if str(p).endswith("py312.exe") else (3, 10))
    proj = project(tmp_path / "proj")
    try:
        assert pb.pick_interpreter(proj) == str(p312)
        assert pb.resolve_declared_interpreter(proj, "3.10") == str(p310)
    finally:
        mp.undo()


def test_build_keeps_identity_gate_before_interpreter_resolution():
    src = inspect.getsource(pb.build)
    assert "declared_python_minor" in inspect.signature(pb.build).parameters
    assert "require_unprivileged_identity" in src
    assert "resolve_declared_interpreter" in src
    assert src.index("require_unprivileged_identity") < src.index("resolve_declared_interpreter")
    assert "base_python = pick_interpreter(project)" not in src


def test_main_passes_the_declared_minor():
    main_src = inspect.getsource(pb.main)
    assert "declared_python_minor=_declared_minor_for" in main_src


def test_real_ratified_sidecar_is_accepted_and_alexa_excluded():
    if not pb.DECLARED_ENVIRONMENTS_SIDECAR.is_file() or not pb.DECLARED_ENVIRONMENTS_PROVENANCE.is_file():
        pytest.skip(
            f"External owner-ratified sidecar integration prerequisite missing on this runner: {pb.DECLARED_ENVIRONMENTS_SIDECAR}"
        )
    loaded = pb.load_declared_environments()
    assert len(loaded) == 10
    assert ALEXA not in loaded
    assert set(loaded.values()) <= {"3.9", "3.10", "3.11"}
