"""File-based subagent registry (`.rob/agents/*.md`).

Covers the audited contract: new agents only (built-ins win on collision),
fail-open on a missing/unreadable directory, unknown keys ignored, `mutable`
defaulting to false, and the lazy wiring into `system_prompts.build`,
`model_registry.get_model`, and the agent roster.
"""

from __future__ import annotations

import textwrap

import pytest

import runtime_v2.services.subagent_registry as reg
from runtime_v2.prompts.system_prompts import _TOOL_DEFINITIONS, build
from runtime_v2.services.model_registry import get_model

_DB_MIGRATOR = """\
---
name: db-migrator
description: Plans and writes safe database migrations.
tools: filesystem, sandbox_repl, final
model: some-local-model
mode: coding
mutable: false
budget: 8000
---
You are a database migration specialist. Never drop a column without a backup.
"""


@pytest.fixture
def agents_dir(tmp_path, monkeypatch):
    """Point the registry at an empty tmp agents dir and clear its cache."""
    root = tmp_path / ".rob" / "agents"
    monkeypatch.setattr(reg, "_AGENTS_ROOT", str(root))
    monkeypatch.setattr(reg, "_CACHE", (0.0, []))
    return root


def _write(root, filename: str, content: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / filename).write_text(textwrap.dedent(content), encoding="utf-8")


# --------------------------------------------------------------------------
# registry (fail-open / parsing)
# --------------------------------------------------------------------------


def test_missing_directory_is_empty_and_fail_open(agents_dir):
    assert not agents_dir.exists()
    assert reg.list_subagents() == []
    assert reg.get_subagent("db-migrator") is None
    assert reg.tools_for("db-migrator") is None
    # built-in build is completely unchanged with no file agents present
    assert "filesystem" in build("coder")


def test_parses_frontmatter_body_and_tools(agents_dir):
    _write(agents_dir, "db-migrator.md", _DB_MIGRATOR)
    sub = reg.get_subagent("db-migrator")
    assert sub is not None
    assert sub["description"] == "Plans and writes safe database migrations."
    assert sub["tools"] == ["filesystem", "sandbox_repl", "final"]
    assert sub["model"] == "some-local-model"
    assert sub["mode"] == "coding"
    assert sub["mutable"] is False
    assert sub["budget"] == 8000
    assert "database migration specialist" in sub["body"]


def test_mutable_defaults_false_and_true_is_parsed(agents_dir):
    _write(
        agents_dir,
        "a.md",
        "---\nname: a\ndescription: d\n---\nbody\n",
    )
    _write(
        agents_dir,
        "b.md",
        "---\nname: b\ndescription: d\nmutable: true\n---\nbody\n",
    )
    assert reg.get_subagent("a")["mutable"] is False
    assert reg.get_subagent("b")["mutable"] is True


def test_malformed_file_is_skipped_not_crashed(agents_dir):
    _write(agents_dir, "good.md", "---\nname: good\ndescription: ok\n---\nbody\n")
    _write(agents_dir, "bad.md", "no frontmatter here at all\n")
    (agents_dir / "boom.md").mkdir()  # an unreadable *.md entry
    # A single bad/unreadable file must not abort the whole scan.
    names = {s["name"] for s in reg.list_subagents()}
    assert "good" in names  # bad.md falls back to its stem; boom.md is skipped


def test_name_falls_back_to_filename_stem(agents_dir):
    _write(agents_dir, "my-helper.md", "---\ndescription: helper\n---\nbody\n")
    assert reg.get_subagent("my-helper") is not None


def test_path_traversal_name_is_rejected(agents_dir):
    """A `.rob/agents` file is untrusted repo content: a `name` with a path
    separator must never be able to steer a write outside the agents dir."""
    _write(
        agents_dir,
        "evil.md",
        "---\nname: ../../hooks\ndescription: x\nmutable: true\n---\nbody\n",
    )
    assert reg.list_subagents() == []
    assert reg.get_subagent("../../hooks") is None


# --------------------------------------------------------------------------
# built-ins win
# --------------------------------------------------------------------------


def test_file_cannot_override_a_builtin_agent(agents_dir):
    _write(
        agents_dir,
        "coder.md",
        "---\nname: coder\ndescription: HIJACKED\ntools: sandbox_repl\n---\nEVIL\n",
    )
    builtin = build("coder")
    assert "HIJACKED" not in builtin
    assert "EVIL" not in builtin
    assert "filesystem" in builtin  # the real coder tools


def test_merge_into_roster_adds_only_new_names(agents_dir):
    _write(agents_dir, "db-migrator.md", _DB_MIGRATOR)
    _write(
        agents_dir,
        "coder.md",
        "---\nname: coder\ndescription: HIJACKED\n---\nEVIL\n",
    )
    roster = {"coder": {"id": "coder", "role": "coder", "config": {}}}
    added = reg.merge_into_roster(roster)
    assert added == 1
    assert "db-migrator" in roster
    assert roster["db-migrator"]["config"]["source"] == "file"
    assert roster["db-migrator"]["config"]["mutable"] is False
    assert roster["coder"].get("description") != "HIJACKED"


def test_merge_marks_mutable(agents_dir):
    _write(
        agents_dir,
        "tuner.md",
        "---\nname: tuner\ndescription: d\nmutable: true\n---\nbody\n",
    )
    roster: dict = {}
    reg.merge_into_roster(roster)
    assert roster["tuner"]["config"]["mutable"] is True


# --------------------------------------------------------------------------
# lazy wiring: build / get_model / tools_for
# --------------------------------------------------------------------------


def test_build_uses_file_body_and_tools(agents_dir):
    _write(agents_dir, "db-migrator.md", _DB_MIGRATOR)
    prompt = build("db-migrator")
    assert "database migration specialist" in prompt
    assert _TOOL_DEFINITIONS["sandbox_repl"] in prompt  # declared tool surfaced
    assert _TOOL_DEFINITIONS["web_fetch"] not in prompt  # not granted


def test_build_file_agent_without_tools_gets_safe_default(agents_dir):
    _write(agents_dir, "plain.md", "---\nname: plain\ndescription: d\n---\nbody\n")
    prompt = build("plain")
    assert _TOOL_DEFINITIONS["filesystem"] in prompt  # default tool set
    assert _TOOL_DEFINITIONS["sandbox_repl"] not in prompt


def test_get_model_uses_file_agent_model(agents_dir):
    _write(agents_dir, "db-migrator.md", _DB_MIGRATOR)
    assert get_model("db-migrator") == ("some-local-model", "llama")
    # built-in unchanged
    assert get_model("coder") == ("robs4b", "llama")
    # unknown + no file dir -> default
    assert get_model("does-not-exist") == ("robs4b", "llama")


def test_tools_for_file_agent(agents_dir):
    _write(agents_dir, "db-migrator.md", _DB_MIGRATOR)
    assert reg.tools_for("db-migrator") == ["filesystem", "sandbox_repl", "final"]
    assert reg.tools_for("coder") is None  # built-in -> not a file agent
