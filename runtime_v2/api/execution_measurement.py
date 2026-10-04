"""Typed mutation measurement from ATIF execution telemetry (authoritative seam).

Why this exists
---------------
The trajectory producer emits **bare tool names** (``filesystem``, ``sandbox_repl``)
in ``tool_order``. Inferring "an edit was attempted" by searching those names for
``write``/``patch``/``edit`` is a semantic contract mismatch: it is always False on
real data. The operation-level signal exists one layer down, at the execution
boundary, and is persisted in the ATIF step record
(``data/trajectories/{run_id}.jsonl``):

* ``tool_calls[].function_name`` — the dispatched tool/action
* ``tool_calls[].arguments.operation`` — the filesystem operation the executor
  received (``read`` / ``write`` / ``patch`` / ...)
* ``observation.results[].extra.ok`` — whether that dispatch returned success

This module converts that telemetry into two **coarse tri-state booleans**. It is
the only thing that crosses into learning evidence: no path, file name, content,
patch, command, argument, task prompt or evaluator prose is ever returned.

Semantics (kept deliberately distinct — never collapsed):

* ``mutation_attempted`` — a source-mutating operation was actually dispatched.
  ``True`` = dispatched; ``False`` = tool calls happened but no source-mutating
  operation and no indirect-mutation-capable tool was used; ``None`` = UNKNOWN
  (an indirect tool such as ``sandbox_repl``/``mcp`` could have edited).
* ``mutation_succeeded`` — a dispatched source-mutating operation returned
  ``ok=True``. ``None`` when unknown.
* ``source_changed`` — a SEPARATE, independent post-run repository measurement
  (git diff), computed by the caller. Never derived from the above.
"""
from __future__ import annotations

from typing import Iterable

#: filesystem operations that mutate the source tree.
_SOURCE_MUTATION_OPS = frozenset({"write", "create", "patch", "edit", "modify", "replace"})

#: filesystem operations that cannot mutate the source tree.
_READ_ONLY_OPS = frozenset({
    "read", "read_all", "glob", "list", "grep", "search", "exists", "stat", "ls", "find",
})

#: tools that can mutate source INDIRECTLY (opaque shell / MCP / git). When one
#: of these is used without a structured filesystem mutation, we cannot claim
#: that no mutation occurred, so the measurement is UNKNOWN (never a guessed
#: False). ``git`` is here because commit/checkout change repository state.
_INDIRECT_MUTATION_TOOLS = frozenset({"sandbox_repl", "mcp", "git"})

#: tools that cannot change the source tree at all.
_NON_MUTATING_TOOLS = frozenset({
    "lsp", "web_search", "web_fetch", "semantic_search", "github_research",
    "system", "screen", "email", "playwright", "todo", "final", "remember",
    "deprecate_memory", "ask_user", "delegate",
})

#: The tool whose ``arguments.operation`` carries structured operation semantics.
_STRUCTURED_MUTATION_TOOL = "filesystem"


def measure_mutation(
    step_tool_calls: Iterable[dict],
) -> tuple[bool | None, bool | None]:
    """Return ``(mutation_attempted, mutation_succeeded)``, each ``True``/``False``/``None``.

    ``None`` is UNKNOWN (fail closed), never a guessed ``False``. ``step_tool_calls``
    is the ATIF tool-call list, each entry optionally carrying
    ``extra.observation_ok``.
    """
    saw_call = False
    saw_source_mutation = False
    any_success = False
    any_failure = False
    saw_uncertain = False  # indirect or unrecognized -> cannot claim "no mutation"

    for call in step_tool_calls:
        if not isinstance(call, dict):
            continue
        saw_call = True
        name = str(call.get("function_name") or "").strip().lower()
        args = call.get("arguments")
        op = ""
        if isinstance(args, dict):
            op = str(args.get("operation") or "").strip().lower()
        extra = call.get("extra")
        ok = extra.get("observation_ok") if isinstance(extra, dict) else None

        if name == _STRUCTURED_MUTATION_TOOL:
            if op in _SOURCE_MUTATION_OPS:
                saw_source_mutation = True
                if ok is True:
                    any_success = True
                elif ok is False:
                    any_failure = True
            elif op not in _READ_ONLY_OPS:
                # A filesystem op we do not recognize: fail closed.
                saw_uncertain = True
        elif name in _INDIRECT_MUTATION_TOOLS:
            saw_uncertain = True
        elif name in _NON_MUTATING_TOOLS:
            pass
        else:
            # Unrecognized tool: cannot rule out a mutation. Fail closed.
            saw_uncertain = True

    if not saw_call:
        return None, None
    if saw_source_mutation:
        succeeded = True if any_success else (False if any_failure else None)
        return True, succeeded
    if saw_uncertain:
        return None, None
    return False, False
