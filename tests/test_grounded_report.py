"""Deterministic grounded-report assembler (2026-09-10).

Replace the thin max-turns fallback with a real report assembled from the read
ledger (path + line count + top-level symbols). No LLM text is used, so the
output cannot name an unread file or invent a finding — the deep-research-backed
"model selects, code materializes" / citation-grounding-through-architecture
pattern (arXiv:2512.12117).
"""

from __future__ import annotations

from pathlib import Path

from runtime_v2.api._agent_helpers import _build_grounded_report


def test_report_lists_only_ledger_files():
    ledger = {"runtime_v2/api/_agent_state.py", "runtime_v2/api/_agent_helpers.py"}
    report = _build_grounded_report(ledger)
    assert "runtime_v2/api/_agent_state.py" in report
    assert "runtime_v2/api/_agent_helpers.py" in report
    # A file NOT in the ledger must never appear (the anti-fabrication property).
    assert "models.py" not in report
    assert "code_analysis_report.txt" not in report


def test_report_includes_top_level_symbols_for_py():
    report = _build_grounded_report({"runtime_v2/api/_agent_state.py"})
    assert "top-level: _CallState" in report
    assert "lines" in report


def test_report_handles_missing_file_without_crashing():
    report = _build_grounded_report({"definitely_not_a_real_file_xyz.py"})
    assert "definitely_not_a_real_file_xyz.py" in report
    assert "skipped" in report or "could not be re-read" in report


def test_report_empty_ledger_is_honest():
    report = _build_grounded_report(set())
    assert "No files were read" in report


def test_report_absolute_path(tmp_path: Path):
    f = tmp_path / "sample.py"
    f.write_text("def alpha():\n    pass\n\nclass Beta:\n    pass\n", encoding="utf-8")
    report = _build_grounded_report({str(f)})
    assert "alpha" in report and "Beta" in report


def test_report_renders_validated_findings_under_their_file():
    ledger = {"runtime_v2/api/_agent_state.py"}
    report = _build_grounded_report(
        ledger,
        findings={"runtime_v2/api/_agent_state.py": "read budget fields are not reset"},
    )
    assert "- runtime_v2/api/_agent_state.py" in report
    assert "finding: read budget fields are not reset" in report
    # the finding is nested under the file, not emitted as a bare claim
    idx_file = report.index("runtime_v2/api/_agent_state.py")
    idx_finding = report.index("finding:")
    assert idx_finding > idx_file


def test_finding_for_non_ledger_file_never_renders():
    # A finding keyed to a file NOT in the read ledger must not appear at all —
    # the anti-fabrication property (model selects, code materializes only
    # validated entries).
    report = _build_grounded_report(
        {"runtime_v2/api/_agent_state.py"},
        findings={"models.py": "unused variable on line 56"},
    )
    assert "models.py" not in report
    assert "unused variable" not in report

