"""Tests for the metadata-only on-disk skill registry + system-prompt injection.

Covers:
  1. Frontmatter parsing and `list_metadata()` shape on a synthetic tmp skills/.
  2. Fail-open behavior: empty/missing skills dir -> empty list, never raises.
  3. A skill file with a name but no description is omitted (not shown as a
     useless empty hint).
  4. Injection gating: only roles in the minimal allowlist get [AVAILABLE SKILLS]
     in their compiled system prompt (build-time, metadata-only — no bodies, so
     no schema/grammar/action surface is involved).
"""

import pytest

from runtime_v2.prompts import system_prompts as sp
from runtime_v2.services import skills_registry as sr


@pytest.fixture
def skills_tree(tmp_path, monkeypatch):
    """Point the registry at a tmp skills/ tree and reset its scan cache."""
    root = tmp_path / "skills"
    monkeypatch.setattr(sr, "_SKILLS_ROOT", str(root))
    # Reset the (mtime, list) cache so the next list_metadata() re-scans.
    monkeypatch.setattr(sr, "_METADATA_CACHE", (0.0, []))
    return root


def _write_skill(root, folder, name, description, body=""):
    d = root / folder
    d.mkdir(parents=True)
    header = "---\n"
    if name:
        header += f"name: {name}\n"
    if description:
        header += f"description: {description}\n"
    header += "---\n"
    (d / "SKILL.md").write_text(header + body, encoding="utf-8")


def test_parses_real_frontmatter_metadata(skills_tree):
    _write_skill(
        skills_tree,
        "troubleshooting-history",
        "troubleshooting-history",
        "Proven failure patterns and what to check first.",
        body="# Digest\nReal body text an agent would NOT see in metadata.\n",
    )
    meta = sr.list_metadata()
    assert meta == [
        {
            "name": "troubleshooting-history",
            "description": "Proven failure patterns and what to check first.",
        }
    ]
    # Metadata-only guarantee: bodies are never surfaced by list_metadata().
    joined = " ".join(
        f"{m['name']}{m['description']}" for m in meta
    )
    assert "# Digest" not in joined
    assert "Real body text" not in joined


def test_missing_skills_dir_is_fail_open(skills_tree):
    # skills_tree points at a non-existent path (fixture only creates it on
    # _write_skill); list_metadata must return [] without raising.
    assert not skills_tree.exists()
    assert sr.list_metadata() == []


def test_empty_skills_dir_is_fail_open(skills_tree):
    skills_tree.mkdir(parents=True)
    assert sr.list_metadata() == []


def test_skill_without_description_omitted(skills_tree):
    _write_skill(
        skills_tree,
        "bare",
        "bare",
        "",  # no description
        body="Random content with no frontmatter description.\n",
    )
    assert sr.list_metadata() == []


def test_ignores_non_skill_files(skills_tree):
    # A stray file directly under skills/ (not a <name>/SKILL.md) is skipped.
    skills_tree.mkdir(parents=True)
    (skills_tree / "NOT_A_SKILL.md").write_text("---\nname: x\n---\n", encoding="utf-8")
    _write_skill(
        skills_tree, "real", "real", "A real skill with a description."
    )
    names = [m["name"] for m in sr.list_metadata()]
    assert names == ["real"]


def test_injection_gated_to_debugger_only():
    d = sp.build("debugger")
    assert "[AVAILABLE SKILLS" in d
    # Coordinator is not in the minimal allowlist and must NOT get the block.
    assert "[AVAILABLE SKILLS" not in sp.build("coordinator")
    # coder/researcher are not in the minimal allowlist yet (watch-oracle).
    assert "[AVAILABLE SKILLS" not in sp.build("coder")
    assert "[AVAILABLE SKILLS" not in sp.build("code_analyzer")
