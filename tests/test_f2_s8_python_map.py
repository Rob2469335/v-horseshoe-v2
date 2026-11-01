"""G26 mitigation: owner-supplied interpreter map, no discovery fallback."""

from __future__ import annotations

import pathlib

import pytest

from qwen_train import f2_s8_build_executor as X
from qwen_train import f2_s8_pilot_build as PB

TASK = "owner__widget-1"


def _project(tmp_path: pathlib.Path, requires: str = ">=3.8") -> pathlib.Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        f'[project]\nname = "x"\nversion = "0"\nrequires-python = "{requires}"\n',
        encoding="utf-8",
    )
    return project


def test_parse_accepts_and_normalises_entries():
    mapping = PB.parse_python_map(["3.10=C:\\Python310\\python.exe"])
    assert mapping == {"3.10": "C:\\Python310\\python.exe"}
    assert PB.parse_python_map(None) == {}


@pytest.mark.parametrize("bad", [
    "",
    "3.10",
    "3.10=",
    "=C:\\x\\python.exe",
    "py3.10=C:\\x\\python.exe",
    "3.10=relative\\python.exe",
])
def test_parse_rejects_malformed_entries(bad):
    with pytest.raises(PB.DeclaredInterpreterError):
        PB.parse_python_map([bad])


def test_parse_rejects_duplicate_minor():
    with pytest.raises(PB.DeclaredInterpreterError, match="duplicate"):
        PB.parse_python_map(["3.10=C:\\a\\python.exe", "3.10=C:\\b\\python.exe"])


def test_mapped_minor_uses_the_path_without_discovery(tmp_path, monkeypatch):
    project = _project(tmp_path)
    interpreter = tmp_path / "python.exe"
    interpreter.write_bytes(b"stub")

    def _boom(*a, **k):  # discovery must not run for a mapped minor
        raise AssertionError("discovery was used for a mapped minor")

    monkeypatch.setattr(PB, "pick_interpreter", _boom)
    monkeypatch.setattr(PB, "_version_of", lambda path: (3, 10))
    chosen = PB.resolve_declared_interpreter(
        project, "3.10", python_map={"3.10": str(interpreter)}
    )
    assert chosen == str(interpreter)


def test_mapped_missing_path_fails_closed(tmp_path, monkeypatch):
    project = _project(tmp_path)
    monkeypatch.setattr(PB, "pick_interpreter", lambda *a, **k: "discovery")
    with pytest.raises(PB.DeclaredInterpreterError, match="missing"):
        PB.resolve_declared_interpreter(
            project, "3.10", python_map={"3.10": str(tmp_path / "nope.exe")}
        )


def test_mapped_minor_still_obeys_project_requires_python(tmp_path):
    project = _project(tmp_path, requires=">=3.11")
    interpreter = tmp_path / "python.exe"
    interpreter.write_bytes(b"stub")
    with pytest.raises(PB.DeclaredInterpreterError, match="requires-python"):
        PB.resolve_declared_interpreter(
            project, "3.10", python_map={"3.10": str(interpreter)}
        )


def test_unmapped_minor_keeps_discovery(tmp_path, monkeypatch):
    project = _project(tmp_path)
    monkeypatch.setattr(PB, "pick_interpreter", lambda *a, **k: "discovered")
    assert PB.resolve_declared_interpreter(
        project, "3.10", python_map={"3.11": "C:\\Python311\\python.exe"}
    ) == "discovered"


def test_executor_rejects_a_missing_mapped_path(tmp_path):
    with pytest.raises(ValueError, match="existing absolute path"):
        X.HostBuildExecutor(
            object(),
            inputs=tmp_path,
            patches=tmp_path,
            python_map={"3.10": str(tmp_path / "nope.exe")},
        )


def test_executor_passes_the_map_to_build(tmp_path):
    interpreter = tmp_path / "python.exe"
    interpreter.write_bytes(b"stub")
    ws = tmp_path / TASK
    ws.mkdir()
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / f"{TASK}.json").write_text("{}", encoding="utf-8")
    captured = {}

    class _Auth:
        selected_task_ids = (TASK,)

    def build_fn(*a, **k):
        captured.update(k)
        return {"task_id": TASK, "workspace": str(ws), "elapsed_s": 0.1}

    ex = X.HostBuildExecutor(
        _Auth(),
        inputs=inputs,
        patches=tmp_path,
        workspace_root=tmp_path,
        build_fn=build_fn,
        classify_fn=lambda exc: "NOISE",
        resolve_url_fn=lambda task, i: "https://example.com/r.git",
        declared_minor_fn=lambda task, i: "3.10",
        collect_fn=lambda *a, **k: (True, "", "ok"),
        python_map={"3.10": str(interpreter)},
    )
    cell = ex.build_one(TASK)
    assert cell.classification == "OBSERVED_COMPATIBLE"
    assert captured["python_map"] == {"3.10": str(interpreter)}


def test_driver_refuses_a_malformed_map_before_any_grant(monkeypatch, capsys):
    import qwen_train.f2_s8_acquisition as acq
    from qwen_train.f2_s8_probe_driver import main as probe_main

    monkeypatch.setattr(acq, "require_unprivileged_identity", lambda entry: None)
    rc = probe_main(["--python-map", "not-a-map"])
    assert rc == 1
    assert "invalid --python-map" in capsys.readouterr().err


def test_zero_padded_keys_are_canonicalised_or_refused():
    # "3.010" can never match a declared minor; it is refused rather than left
    # inert (which would silently re-enable discovery). "03.10" is canonicalised.
    with pytest.raises(PB.DeclaredInterpreterError, match="major.minor"):
        PB.parse_python_map(["3.010=C:\\Python310\\python.exe"])
    assert PB.parse_python_map(["03.10=C:\\Python310\\python.exe"]) == {
        "3.10": "C:\\Python310\\python.exe"
    }


def test_mapped_interpreter_with_the_wrong_version_is_refused(tmp_path, monkeypatch):
    project = _project(tmp_path)
    interpreter = tmp_path / "python.exe"
    interpreter.write_bytes(b"stub")
    monkeypatch.setattr(PB, "_version_of", lambda path: (3, 12))
    with pytest.raises(PB.DeclaredInterpreterError, match="reports 3.12"):
        PB.resolve_declared_interpreter(
            project, "3.10", python_map={"3.10": str(interpreter)}
        )


def test_unusable_mapped_path_fails_as_a_configuration_error(tmp_path):
    project = _project(tmp_path)
    stub = tmp_path / "python.exe"
    stub.write_bytes(b"not an executable")
    with pytest.raises(PB.DeclaredInterpreterError, match="could not be executed"):
        PB.resolve_declared_interpreter(
            project, "3.10", python_map={"3.10": str(stub)}
        )


def test_direct_map_with_a_noncanonical_key_still_matches(tmp_path, monkeypatch):
    project = _project(tmp_path)
    interpreter = tmp_path / "python.exe"
    interpreter.write_bytes(b"stub")
    monkeypatch.setattr(PB, "_version_of", lambda path: (3, 10))
    monkeypatch.setattr(PB, "pick_interpreter", lambda *a, **k: "discovery")
    assert PB.resolve_declared_interpreter(
        project, "3.10", python_map={"03.10": str(interpreter)}
    ) == str(interpreter)


def test_validate_refuses_duplicate_canonical_keys(tmp_path):
    a = tmp_path / "a.exe"
    b = tmp_path / "b.exe"
    a.write_bytes(b"a")
    b.write_bytes(b"b")
    with pytest.raises(PB.DeclaredInterpreterError, match="duplicate"):
        PB.validate_python_map({"3.10": str(a), "03.10": str(b)})


def test_duplicate_canonical_keys_in_the_resolver_fail_closed(tmp_path, monkeypatch):
    project = _project(tmp_path)
    a = tmp_path / "a.exe"
    b = tmp_path / "b.exe"
    a.write_bytes(b"a")
    b.write_bytes(b"b")
    monkeypatch.setattr(PB, "_version_of", lambda path: (3, 10))
    with pytest.raises(PB.DeclaredInterpreterError, match="duplicate"):
        PB.resolve_declared_interpreter(
            project, "3.10", python_map={"3.10": str(a), "03.10": str(b)}
        )


def test_direct_map_relative_path_is_refused(tmp_path, monkeypatch):
    project = _project(tmp_path)
    monkeypatch.setattr(PB, "_version_of", lambda path: (3, 10))
    with pytest.raises(PB.DeclaredInterpreterError, match="absolute"):
        PB.resolve_declared_interpreter(
            project, "3.10", python_map={"3.10": "relative\\python.exe"}
        )


def test_task_deadline_during_the_probe_propagates_unwrapped(tmp_path, monkeypatch):
    project = _project(tmp_path)
    interpreter = tmp_path / "python.exe"
    interpreter.write_bytes(b"stub")

    def _expired(path):
        raise PB.TaskDeadlineExceeded("probe", 0.0, stderr="expired")

    monkeypatch.setattr(PB, "_version_of", _expired)
    with pytest.raises(PB.TaskDeadlineExceeded):
        PB.resolve_declared_interpreter(
            project, "3.10", python_map={"3.10": str(interpreter)}
        )


def test_interpreter_failures_classify_as_interpreter_unavailable():
    assert PB.classify_build_failure(
        PB.DeclaredInterpreterError("mapped interpreter for 3.10 reports 3.12")
    ) == "INTERPRETER_UNAVAILABLE"
