"""Metadata-only on-disk skill registry for always-on agent context.

Reads the repo root `skills/` hierarchy: each skill lives at
`skills/<name>/SKILL.md` with YAML frontmatter carrying (at minimum) `name` and
`description` (a one-line "when to use"). Agents are shown only this compressed
metadata — name + description — at system-prompt build time so they stay aware a
skill exists without paying per-window context cost for its full body.

Deliberately scoped (per audit 2026-09-06):
  - metadata only, never the body, in the always-on injection channel;
  - no network, no YAML dependency (frontmatter parsed with a tiny stdlib parser);
  - fail-open: any scan/parse error or empty skills dir degrades to ``""`` so
    agents keep working (same contract as project_map._load_agents_md).

The full skill body is intentionally NOT loaded here. If evidence later shows an
agent needs a body on demand, that is a separate Runtime-vs-build-time decision
with its own schema-sync cost (see watch-oracle in the build commit) — not this
module's job.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

_SKILLS_ROOT = os.getenv("SWARM_SKILLS_ROOT", "").strip() or str(
    Path(__file__).resolve().parents[2] / "skills"
)

_METADATA_CACHE: tuple[float, list[dict]] = (0.0, [])


def _root_mtime() -> float:
    """Newest mtime under the skills tree so edits invalidate the cache."""
    root = Path(_SKILLS_ROOT)
    latest = 0.0
    try:
        for p in root.rglob("SKILL.md"):
            try:
                latest = max(latest, p.stat().st_mtime)
            except OSError:
                continue
    except OSError:
        return latest
    return latest


def _parse_frontmatter(text: str) -> dict:
    """Minimal YAML-ish frontmatter parser.

    Handles the small, deliberately restricted shape used in these files:
      ---
      name: value
      description: value
      ---
    Returns an empty dict when there is no frontmatter block or it is
    malformed. We do NOT pull in a YAML dependency for this: the authoring
    contract is that values are single-line, unquoted, `key: value` pairs.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out: dict[str, str] = {}
    for raw in lines[1:]:
        stripped = raw.strip()
        if stripped == "---":
            break
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key and value:
            out[key] = value
    return out


def _scan() -> list[dict]:
    """Return [{name, description}] for every skills/<name>/SKILL.md found."""
    root = Path(_SKILLS_ROOT)
    if not root.exists():
        return []
    entries: list[dict] = []
    # Only direct children of skills/ are skills (nesting not supported).
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        skill_file = child / "SKILL.md"
        if not skill_file.exists():
            continue
        try:
            fm = _parse_frontmatter(skill_file.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("skill_scan: failed to read %s: %s", skill_file, exc)
            continue
        name = fm.get("name") or child.name
        description = fm.get("description", "").strip()
        if not description:
            # A skill file with a name but no "when to use" line is not useful as
            # metadata — skip it rather than show an empty hint.
            continue
        entries.append({"name": name, "description": description})
    return entries


def list_metadata() -> list[dict]:
    """Return skill metadata, re-scanning only when the tree changes on disk."""
    global _METADATA_CACHE
    cached_mtime, cached = _METADATA_CACHE
    # Re-scan if the skills tree was touched after the last load, or on the
    # first call (cached_mtime == 0.0).
    current = _root_mtime()
    if cached_mtime == 0.0 or current != cached_mtime:
        _METADATA_CACHE = (current, _scan())
    return _METADATA_CACHE[1]
