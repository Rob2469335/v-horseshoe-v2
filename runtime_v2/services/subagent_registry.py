"""File-based subagent registry: ``.rob/agents/*.md``.

Each subagent file is YAML-ish frontmatter plus a Markdown body::

    ---
    name: db-migrator
    description: Plans and writes safe database migrations.
    tools: filesystem, sandbox_repl, final
    model: robs4b
    mode: coding
    mutable: false
    ---
    <extra role instructions appended to this agent's system prompt>

Contract (audited 2026-09-13):
  - **New agents only.** A file can never override a built-in agent's identity:
    a name collision resolves to the built-in (the registry is only consulted
    when a name is absent from the built-in tables).
  - **Forward-compat.** Unknown frontmatter keys are ignored.
  - **Fail-open.** A missing ``.rob/agents/`` directory, an unreadable file, or
    a malformed frontmatter block degrades to "fewer/no file agents" — the
    built-in roster is never changed (same contract as ``skills_registry`` and
    ``project_map._load_agents_md``).
  - **`mutable` defaults to false.** Static behaviour is identical to today;
    only ``mutable: true`` agents are candidates for the config-evolution path
    (wired separately, opt-in).
  - No YAML dependency: the frontmatter parser handles the small, single-line
    ``key: value`` shape these files use.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

log = logging.getLogger(__name__)

_AGENTS_ROOT = os.getenv("SWARM_AGENTS_ROOT", "").strip() or str(
    Path(__file__).resolve().parents[2] / ".rob" / "agents"
)

# A subagent name must be a simple identifier: never a path (a `.rob/agents`
# file is untrusted repo content, so `name: ../../x` must not be able to steer
# a write outside the agents directory).
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _safe_name(name: str) -> bool:
    return bool(name) and bool(_NAME_RE.match(name)) and ".." not in name


# (tree-mtime, parsed entries) — rescanned only when the directory changes.
_CACHE: tuple[float, list[dict]] = (0.0, [])

_DEFAULT_TOOLS = ["final", "filesystem"]


def _root_sig() -> tuple:
    """Cache signature for the agents dir: the directory's own mtime (changes
    on add/remove/rename) PLUS each file's name+mtime. Keying on the MAX file
    mtime alone missed a deletion of a non-newest file, so a removed agent kept
    being served from cache (and stayed in the running roster)."""
    root = Path(_AGENTS_ROOT)
    try:
        dir_mtime = root.stat().st_mtime
    except OSError:
        return (0.0, ())
    items: list[tuple[str, float]] = []
    try:
        for p in root.glob("*.md"):
            try:
                items.append((p.name, p.stat().st_mtime))
            except OSError:
                items.append((p.name, -1.0))
    except OSError:
        return (dir_mtime, ())
    return (dir_mtime, tuple(sorted(items)))


def _parse_frontmatter(text: str) -> dict:
    """Minimal YAML-ish frontmatter parser (single-line ``key: value`` pairs).

    Returns an empty dict when there is no frontmatter block or it is
    malformed. Values are treated as plain strings; interpretation (list/bool)
    happens in the caller.
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


def _split_body(text: str) -> str:
    """Return the Markdown body after the closing frontmatter fence."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[i + 1 :]).strip()
    return ""


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: str) -> int | None:
    try:
        return int(str(value).strip())
    except TypeError, ValueError:
        return None


def _parse_tools(value: str) -> list[str] | None:
    if not value:
        return None
    parts = value.replace(";", ",").split(",")
    tools = [p.strip() for p in parts if p.strip()]
    return tools or None


def _scan() -> list[dict]:
    """Return one dict per valid ``.rob/agents/*.md`` file."""
    root = Path(_AGENTS_ROOT)
    if not root.exists():
        return []
    entries: list[dict] = []
    for child in sorted(root.glob("*.md")):
        try:
            text = child.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            log.warning("subagent_scan: failed to read %s: %s", child, exc)
            continue
        fm = _parse_frontmatter(text)
        name = (fm.get("name") or child.stem).strip()
        if not _safe_name(name):
            log.warning(
                "subagent_scan: unsafe or empty name %r in %s — skipped", name, child
            )
            continue
        entries.append(
            {
                "name": name,
                "description": fm.get("description", "").strip(),
                "tools": _parse_tools(fm.get("tools", "")),
                "model": fm.get("model") or None,
                "mode": fm.get("mode") or None,
                "mutable": _as_bool(fm.get("mutable", "")),
                "budget": _as_int(fm.get("budget", "")),
                "body": _split_body(text),
                "source": str(child),
            }
        )
    return entries


def list_subagents() -> list[dict]:
    """File-based subagents, re-scanning only when the directory changes."""
    global _CACHE
    cached_sig, cached = _CACHE
    current = _root_sig()
    if cached_sig == 0.0 or current != cached_sig:
        _CACHE = (current, _scan())
    return _CACHE[1]


def get_subagent(name: str) -> dict | None:
    """Return the file-based subagent named ``name``, or None."""
    if not _safe_name(name):
        return None
    for sub in list_subagents():
        if sub.get("name") == name:
            return sub
    return None


def merge_into_roster(agents: dict) -> int:
    """Add file-based agents to an agent roster; built-in names always win.

    Mutates ``agents`` in place (adds only names not already present) and
    returns the number of agents added.
    """
    added = 0
    for sub in list_subagents():
        name = sub.get("name")
        if not name or name in agents:
            continue  # built-in (or already-registered) identity wins
        cfg: dict = {"source": "file", "mutable": bool(sub.get("mutable"))}
        if sub.get("budget") is not None:
            cfg["budget"] = sub["budget"]
        agents[name] = {
            "id": name,
            "role": name,
            "description": sub.get("description", ""),
            "model_role": sub.get("mode") or "fast",
            "config": cfg,
        }
        added += 1
    return added


def tools_for(name: str) -> list[str] | None:
    """Allow-listed tools for a file-based subagent, or None if not file-based."""
    sub = get_subagent(name)
    if sub is None:
        return None
    return sub.get("tools") or list(_DEFAULT_TOOLS)
