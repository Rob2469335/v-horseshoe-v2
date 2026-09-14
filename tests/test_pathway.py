"""Tests for pathway-evidence metrics (qwen_train/pathway.py).

Pins the RETRIEVE -> AVOID -> VERIFIED chain and the coarse error classification
that lets warned and observed failures be matched without brittle string equality.
No pathway capture exists yet, so the tolerant/empty cases are tested too.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "qwen_train"))

import pathway as pw  # noqa: E402


def _step(seq, tool, label="NORMAL_SUCCESS", error=""):
    return {
        "record_type": "step",
        "step_id": seq,
        "tool_calls": [{"function_name": tool, "arguments": {}}],
        "observation": {"results": [{"extra": {"label": label, "error": error}}]},
    }


def _pathway(injected=True, warned=("filesystem:not_found",)):
    return {
        "record_type": "pathway",
        "phase": "retrieve",
        "injected": injected,
        "lessons": [{"lesson_id": "L1", "score": 0.9}],
        "warned_signatures": list(warned),
    }


def _run(pathway=None, steps=None, status="completed"):
    return {
        "steps": steps or [],
        "pathway": pathway or [],
        "summary": {"status": status},
    }


def test_err_class_coarse_mapping():
    assert pw.err_class("Timeout: tool timed out") == "timeout"
    assert pw.err_class("File not found: x.py") == "not_found"
    assert pw.err_class("Authorization DENIED") == "permission"
    assert pw.err_class("Malformed JSON") == "malformed"
    assert pw.err_class("something else") == "other"


def test_step_failure_signatures_only_failures():
    steps = [
        _step(1, "filesystem", "NORMAL_SUCCESS"),
        _step(2, "filesystem", "FAILURE", "File not found: x.py"),
    ]
    assert pw.step_failure_signatures(steps) == {"filesystem:not_found"}


def test_run_metrics_avoided_when_warned_signature_not_observed():
    run = _run(
        pathway=[_pathway(warned=["filesystem:not_found"])],
        steps=[_step(1, "filesystem", "NORMAL_SUCCESS")],
    )
    m = pw.run_metrics(run)
    assert m["retrieved"] is True
    assert m["avoided"] is True
    assert m["verified"] is True


def test_run_metrics_not_avoided_when_warned_failure_repeats():
    run = _run(
        pathway=[_pathway(warned=["filesystem:not_found"])],
        steps=[_step(1, "filesystem", "FAILURE", "File not found: y.py")],
    )
    assert pw.run_metrics(run)["avoided"] is False


def test_pathway_metrics_aggregate():
    warned_ok = _run(
        pathway=[_pathway(warned=["filesystem:not_found"])],
        steps=[_step(1, "filesystem", "NORMAL_SUCCESS")],
    )
    warned_repeat = _run(
        pathway=[_pathway(warned=["filesystem:not_found"])],
        steps=[_step(1, "filesystem", "FAILURE", "File not found: z.py")],
    )
    no_pathway = _run(steps=[_step(1, "filesystem", "NORMAL_SUCCESS")])
    agg = pw.pathway_metrics([warned_ok, warned_repeat, no_pathway])
    assert agg["runs"] == 3
    assert agg["runs_with_retrieval"] == 2
    assert agg["runs_warned"] == 2
    assert agg["avoidance_rate"] == 0.5
    assert agg["pathway_coherence_rate"] == 0.5


def test_pathway_metrics_tolerates_no_capture():
    agg = pw.pathway_metrics([_run(steps=[_step(1, "filesystem")])])
    assert agg["runs_with_retrieval"] == 0
    assert agg["retrieval_rate"] == 0.0
    assert agg["pathway_coherence_rate"] is None


def test_load_run_full_splits(tmp_path):
    p = tmp_path / "r.jsonl"
    lines = [
        json.dumps(_step(1, "filesystem")),
        json.dumps(_pathway()),
        json.dumps({"record_type": "summary", "status": "completed", "run_id": "r"}),
    ]
    p.write_text("\n".join(lines), encoding="utf-8")
    run = pw.load_run_full(p)
    assert len(run["steps"]) == 1
    assert len(run["pathway"]) == 1
    assert run["summary"]["status"] == "completed"
