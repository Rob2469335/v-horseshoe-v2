"""Findings synthesis must draw from EVERY file read this run, not the tail.

Live defect (2026-09-12): the deep-analysis read-floor made the agent read 8
files, but the grounded report's findings covered only the last ~2 — because
`_extract_grounded_findings` was fed only the model's final prose, which is
recency-biased and mentions the last files it read (and sometimes nothing),
while the per-file read CONTENT sat unused in the tool-result turns. These tests
pin the fix: the read material of every ledger file reaches the extractor, so
the report lists (and carries findings for) everything read that run.
"""

from __future__ import annotations

import json
import re

import pytest

from runtime_v2.api._agent_helpers import (
    _build_grounded_report,
    _collect_read_material,
    _anchor_exists,
    _extract_grounded_findings,
)


def _norm(s):
    return str(s).replace("\\", "/").lstrip("./")


def _read_messages(paths):
    """The exact turn shape the agent loop appends for a filesystem read."""
    msgs = []
    for p in paths:
        msgs.append(
            {
                "role": "assistant",
                "content": json.dumps(
                    {"action": "filesystem", "operation": "read", "path": p}
                ),
            }
        )
        msgs.append(
            {
                "role": "user",
                "content": (
                    f"TOOL RESULT (filesystem):\nREAD CONTENT OF {p}\n\nContinue."
                ),
            }
        )
    return msgs


def test_collect_read_material_maps_every_read():
    paths = [f"pkg/mod{i}.py" for i in range(1, 6)]
    material = _collect_read_material(_read_messages(paths), set(paths))
    assert set(material) == set(paths)
    for p in paths:
        assert f"READ CONTENT OF {p}" in material[p]


@pytest.mark.asyncio
async def test_extraction_draws_from_every_read_not_just_tail(monkeypatch, tmp_path):
    # 5 real files so the report can re-read them (line counts + findings).
    paths = []
    for i in range(1, 6):
        f = tmp_path / f"mod{i}.py"
        f.write_text(f"def fn{i}():\n    return {i}\n", encoding="utf-8")
        paths.append(str(f))
    messages = _read_messages(paths)

    # Recency-biased final: names ONLY the last file — the live failure shape.
    tail_prose = f"Analysis: {paths[-1]} looks suspicious."
    captured = {}

    async def fake_extract(model, messages, agent_id=None, **kwargs):
        prompt = messages[-1]["content"]
        captured["prompt"] = prompt
        captured["max_tokens"] = kwargs.get("max_tokens")
        captured["model"] = model
        # Return a finding for every file whose content reached the prompt.
        files = re.findall(r"^### (.*)$", prompt, re.MULTILINE)
        return json.dumps(
            {"findings": [{"file": f, "finding": "evidenced issue"} for f in files]}
        )

    import runtime_v2.services._llm_client as llc

    monkeypatch.setattr(llc, "complete_json_extraction", fake_extract)
    monkeypatch.setattr(llc, "get_litellm_model", lambda agent_id, model: "resolved/x")

    material = _collect_read_material(messages, set(paths))
    findings = await _extract_grounded_findings(
        "m", "code_analyzer", tail_prose, set(paths), read_material=material
    )

    # The extractor must have seen EVERY file's material, not just the tail.
    for p in paths:
        assert f"### {_norm(p)}" in captured["prompt"]
    # The synthesis needs headroom: reasoning models spend small caps entirely
    # on reasoning_content and return empty content (the live regression).
    assert captured["max_tokens"] == 8000
    # The bare agent alias must be RESOLVED before it reaches litellm, else the
    # provider lookup fails and the findings pass silently returns {}.
    assert captured["model"] == "resolved/x"
    # ...and findings cover every file read, not only the last-mentioned.
    assert set(findings) == {_norm(p) for p in paths}

    report = _build_grounded_report(set(paths), findings=findings)
    for p in paths:
        assert _norm(p) in report
    assert report.count("finding:") == len(paths)
    # A file never read must never appear (anti-fabrication property preserved).
    assert "mod_never_read.py" not in report


def test_report_file_list_reflects_everything_read(tmp_path):
    paths = []
    for i in range(1, 6):
        f = tmp_path / f"svc{i}.py"
        f.write_text("def handler():\n    pass\n", encoding="utf-8")
        paths.append(str(f))
    report = _build_grounded_report(set(paths))
    assert f"Examined {len(paths)} file(s):" in report
    for p in paths:
        assert _norm(p) in report


@pytest.mark.asyncio
async def test_report_has_populated_findings_not_just_manifest(monkeypatch, tmp_path):
    """Item 4: a multi-file read must yield a report with REAL findings, not a
    bare file manifest (the live regression: 8 files read, zero findings)."""
    paths = []
    for i in range(1, 5):
        f = tmp_path / f"svc{i}.py"
        f.write_text("import os\n\ndef handler():\n    return os.system('x')\n")
        paths.append(str(f))

    async def fake_extract(model, messages, agent_id=None, **kwargs):
        files = re.findall(r"^### (.*)$", messages[-1]["content"], re.MULTILINE)
        return json.dumps(
            {
                "findings": [
                    {
                        "file": f,
                        "finding": "unguarded os.system",
                        "evidence": "os.system('x')",
                    }
                    for f in files
                ]
            }
        )

    import runtime_v2.services._llm_client as llc

    monkeypatch.setattr(llc, "complete_json_extraction", fake_extract)
    monkeypatch.setattr(llc, "get_litellm_model", lambda agent_id, model: "resolved/x")

    findings = await _extract_grounded_findings(
        "m",
        "code_analyzer",
        "Read the files.",
        set(paths),
        read_material=_collect_read_material(_read_messages(paths), set(paths)),
    )
    report = _build_grounded_report(set(paths), findings=findings)
    assert "finding:" in report, "report must carry findings, not just a manifest"
    assert report.count("finding:") >= 1
    assert "unguarded os.system" in report
    # ...while the accurate file list is preserved.
    assert f"Examined {len(paths)} file(s):" in report


def test_anchor_exists_matches_verbatim_whitespace_normalized():
    content = 'def handler():\n    return os.system("x")\n'
    assert _anchor_exists('os.system("x")', content) is True
    # whitespace normalization across a newline
    assert _anchor_exists("return  os.system", content) is True
    # a fabricated anchor is rejected
    assert _anchor_exists("subprocess.run", content) is False
    assert _anchor_exists("", content) is False
    assert _anchor_exists("ab", content) is False


def test_short_common_word_anchor_is_not_evidence():
    content = 'def handler():\n    return os.system("x")\nimport os\n'
    # "return"/"import"/"value" are present but prove nothing about any claim.
    assert _anchor_exists("return", content) is False
    assert _anchor_exists("import", content) is False
    assert _anchor_exists("value", content) is False


def test_elided_two_span_anchor_is_rejected():
    content = 'def handler():\n    return os.system("x")\n'
    # Two real but non-contiguous spans joined by an ellipsis must NOT pass.
    assert _anchor_exists('def handler ... os.system("x")', content) is False


@pytest.mark.asyncio
async def test_unanchored_finding_is_marked_not_presented_as_grounded(
    monkeypatch, tmp_path
):
    f = tmp_path / "svc.py"
    f.write_text("def handler():\n    return 1\n", encoding="utf-8")
    lines = [
        {
            "role": "assistant",
            "content": json.dumps(
                {"action": "filesystem", "operation": "read", "path": str(f)}
            ),
        },
        {"role": "user", "content": "TOOL RESULT (filesystem):\ncode\n\nContinue."},
    ]

    async def fake_extract(model, messages, agent_id=None, **kwargs):
        return json.dumps(
            {
                "findings": [
                    {
                        "file": str(f),
                        "finding": "hallucinated bug",
                        "evidence": "subprocess.run not in this file",
                    }
                ]
            }
        )

    import runtime_v2.services._llm_client as llc

    monkeypatch.setattr(llc, "complete_json_extraction", fake_extract)
    monkeypatch.setattr(llc, "get_litellm_model", lambda agent_id, model: "resolved/x")

    findings = await _extract_grounded_findings(
        "m",
        "code_analyzer",
        "x",
        {str(f)},
        read_material=_collect_read_material(lines, {str(f)}),
    )
    text = findings[_norm(str(f))]
    assert text.startswith("[UNANCHORED")
    report = _build_grounded_report({str(f)}, findings=findings)
    assert "[UNANCHORED" in report


@pytest.mark.asyncio
async def test_anchored_finding_renders_without_unanchored_marker(
    monkeypatch, tmp_path
):
    f = tmp_path / "svc.py"
    f.write_text("def handler():\n    return 1\n", encoding="utf-8")
    lines = [
        {
            "role": "assistant",
            "content": json.dumps(
                {"action": "filesystem", "operation": "read", "path": str(f)}
            ),
        },
        {"role": "user", "content": "TOOL RESULT (filesystem):\ncode\n\nContinue."},
    ]

    async def fake_extract(model, messages, agent_id=None, **kwargs):
        return json.dumps(
            {
                "findings": [
                    {
                        "file": str(f),
                        "finding": "handler returns 1",
                        "evidence": "def handler():",
                    }
                ]
            }
        )

    import runtime_v2.services._llm_client as llc

    monkeypatch.setattr(llc, "complete_json_extraction", fake_extract)
    monkeypatch.setattr(llc, "get_litellm_model", lambda agent_id, model: "resolved/x")

    findings = await _extract_grounded_findings(
        "m",
        "code_analyzer",
        "x",
        {str(f)},
        read_material=_collect_read_material(lines, {str(f)}),
    )
    assert not findings[_norm(str(f))].startswith("[UNANCHORED")


@pytest.mark.asyncio
async def test_material_covers_every_file_at_deep_budget(monkeypatch, tmp_path):
    # The 8-14 file deep budget must not starve later files from the material.
    paths = []
    for i in range(1, 15):
        f = tmp_path / f"mod{i:02d}.py"
        f.write_text("".join(f"line {j}\n" for j in range(200)), encoding="utf-8")
        paths.append(str(f))
    captured = {}

    async def fake_extract(model, messages, agent_id=None, **kwargs):
        captured["prompt"] = messages[-1]["content"]
        return json.dumps({"findings": []})

    import runtime_v2.services._llm_client as llc

    monkeypatch.setattr(llc, "complete_json_extraction", fake_extract)
    monkeypatch.setattr(llc, "get_litellm_model", lambda agent_id, model: "resolved/x")

    await _extract_grounded_findings(
        "m",
        "code_analyzer",
        "x",
        set(paths),
        read_material=_collect_read_material(_read_messages(paths), set(paths)),
    )
    for p in paths:
        assert f"### {_norm(p)}" in captured["prompt"], p


def test_report_surfaces_unanchored_count(tmp_path):
    paths = []
    for i in range(1, 3):
        f = tmp_path / f"svc{i}.py"
        f.write_text("def handler():\n    return 1\n", encoding="utf-8")
        paths.append(str(f))
    findings = {
        paths[0]: "[UNANCHORED — no matching text found in file] ghost bug",
        paths[1]: "real anchored finding",
    }
    report = _build_grounded_report(set(paths), findings=findings)
    assert "Findings: 2, 1 unanchored." in report


def test_report_reports_zero_unanchored(tmp_path):
    f = tmp_path / "svc.py"
    f.write_text("def handler():\n    pass\n", encoding="utf-8")
    report = _build_grounded_report({str(f)}, findings={str(f): "ok"})
    assert "Findings: 1, 0 unanchored." in report


@pytest.mark.asyncio
async def test_fabricated_claim_with_trivial_anchor_is_marked(monkeypatch, tmp_path):
    # Audit exploit: a fabricated claim whose evidence is a real but trivial
    # token ("return") must NOT pass as grounded.
    f = tmp_path / "svc.py"
    f.write_text("def handler():\n    return 1\n", encoding="utf-8")
    lines = [
        {
            "role": "assistant",
            "content": json.dumps(
                {"action": "filesystem", "operation": "read", "path": str(f)}
            ),
        },
        {"role": "user", "content": "TOOL RESULT (filesystem):\ncode\n\nContinue."},
    ]

    async def fake_extract(model, messages, agent_id=None, **kwargs):
        return json.dumps(
            {
                "findings": [
                    {
                        "file": str(f),
                        "finding": "CRITICAL RCE: runs os.system on import.",
                        "evidence": "return",
                    }
                ]
            }
        )

    import runtime_v2.services._llm_client as llc

    monkeypatch.setattr(llc, "complete_json_extraction", fake_extract)
    monkeypatch.setattr(llc, "get_litellm_model", lambda agent_id, model: "resolved/x")

    findings = await _extract_grounded_findings(
        "m",
        "code_analyzer",
        "x",
        {str(f)},
        read_material=_collect_read_material(lines, {str(f)}),
    )
    assert findings[_norm(str(f))].startswith("[UNANCHORED")
