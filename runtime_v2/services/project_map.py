"""Build a compact project map from AGENTS.md for injection into agent system prompts.

The agent loop has the same "map the codebase before acting" discipline that a
human maintainer (or opencode) uses: AGENTS.md is the ground truth for module
layout, key files, and conventions. This module distills it into a compact
context block so agents never need to guess file paths.

Guarded: any parse error degrades to an empty string (agents keep working).
"""
from __future__ import annotations
import logging
import os
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

_AGENTS_MD = os.getenv("AGENTS_MD_PATH", "").strip() or str(
    Path(__file__).resolve().parents[2] / "AGENTS.md"
)

_MAX_CHARS = 6000


@lru_cache(maxsize=1)
def _load_agents_md() -> str:
    try:
        return Path(_AGENTS_MD).read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        log.warning("Failed to read AGENTS.md (%s): %s", _AGENTS_MD, exc)
        return ""


def _pick_sections(text: str) -> str:
    """Extract the Architecture Overview + Module Map table blocks.

    Returns a compact snapshot of the key file->role tables so the agent knows
    where the important files live without reading the whole 500-line doc.
    """
    lines = text.splitlines()

    picked: list[str] = []
    current_section = ""
    capture = False
    for raw in lines:
        line = raw.strip()
        if line.startswith("## "):
            current_section = line[3:].strip().lower()
            capture = current_section in (
                "architecture overview",
                "module map",
            )
            if capture:
                picked.append(raw)
            continue
        if line.startswith("### "):
            # Keep sub-headers inside Module Map (e.g. swarm_os/core/)
            capture = current_section == "module map"
            if capture:
                picked.append(raw)
            continue
        if capture:
            # Keep table rows and code-ish lines; drop prose paragraphs.
            if line.startswith("|") or line.startswith("```"):
                picked.append(raw)
            elif line.startswith("- **") or line.startswith("- `"):
                picked.append(raw)
            elif line.startswith("#### ") or line.startswith("----"):
                picked.append(raw)

    block = "\n".join(picked).strip()
    # Reorder so the sections agents touch most come first (runtime_v2 before
    # swarm_os before the rest), protecting them from truncation.
    priority = ("runtime_v2", "swarm_os")
    def _key(block_str: str) -> int:
        low = block_str.lower()
        for i, p in enumerate(priority):
            if p in low:
                return i
        return len(priority)
    sub_blocks = []
    current: list[str] = []
    for raw in block.splitlines():
        if raw.startswith("### "):
            if current:
                sub_blocks.append("\n".join(current))
            current = [raw]
        else:
            current.append(raw)
    if current:
        sub_blocks.append("\n".join(current))
    sub_blocks.sort(key=_key)
    block = "\n\n".join(sub_blocks).strip()
    if len(block) > _MAX_CHARS:
        block = block[:_MAX_CHARS] + "\n...[PROJECT MAP TRUNCATED]..."
    return block


def build_project_map() -> str:
    """Return the condensed AGENTS.md project map (empty string on failure)."""
    text = _load_agents_md()
    if not text:
        return ""
    return _pick_sections(text)
