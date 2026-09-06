---
name: troubleshooting-history
description: Proven failure patterns in this codebase and what to check first when a recurring error class shows up.
---

# Troubleshooting History

Digested failure patterns from real incidents. These are NOT changelog entries —
they are "when you see symptom X, the established root cause class is usually Y,
so check Z before re-deriving the bug from scratch." Read the call site before
patching; every one is a pattern, not a verdict on the current line.

## Schema / dispatch drift (SWARM_GRAMMAR_DECODE related failures)

The tool-decision layer has a hard sync contract: `TOOL_CALL_SCHEMA` in
`runtime_v2/services/_llm_parser.py` and the GBNF mirror
`TOOL_DECISION_JSON_SCHEMA` in `runtime_v2/services/_grammar_schema.py` must stay
byte-aligned. `tests/test_grammar_decode.py::test_schema_remains_synced` fails
loudly on divergence (it asserts the action enum + property set match and the
enum length is 14).

What to check first: if an agent can't reach an action it's supposed to have, or
a grammar-constrained decision fails, verify the action is in BOTH
`_llm_parser.py::TOOL_CALL_SCHEMA["properties"]["action"]["enum"]` AND
`_grammar_schema.py::TOOL_DECISION_JSON_SCHEMA`'s mirrored enum, AND the role's
`_AGENT_TOOLS` list in `runtime_v2/prompts/system_prompts.py`. A tool registered
in `tool_executor.run()` and classified in `approval_registry.py` can still be
dead for the LLM decision loop if it's missing from any of those three. The enum
length assertion (`==14`) in the sync test is a guardrail — bump it deliberately
when adding an action, never incidentally.

Do NOT "fix" a schema mismatch by weakening the sync test. Add the action to both
schema files and update the length assertion together.

## MCP tool registration

External MCP tools load through `ExternalMCPClientManager`. Two historical traps:
`mcp_register` args must be a list of strings with no shell metacharacters
(`&&`, `;`, `|`, backtick, `$(`) — the allowlist of launchers is npx/node/
python/python3/uvx only; and a non-string payload (an int, a dict) bypassed the
old guard. If an MCP tool 404s or a registration is rejected, check (a) args are
strings with no metachars, (b) the launcher is allowlisted, (c) the server
actually responds (a `.models`/tool-list probe) rather than trusting the config.

Do NOT loosen the launcher allowlist or accept shell metacharacters.

## Off-by-one / clock-mixing in time-based checks

Recurring bug class: wall-clock vs monotonic mixing, and `_now()` reading the
real clock while a test monkeypatches a different source. Symptom: a scheduling
or "is due" check passes only at certain times of day. What to check first: does
the code compute against `time.time()` (wall) where the fixture patches a
specific epoch? Use one clock consistently; make time-dependent tests pass an
explicit epoch rather than depending on when the suite runs.

## Deterministic-loop and premature-final traps

Agents (especially small local models) burn turns on: re-running the same failed
tool call verbatim, `action=final` before actually doing the work, or a
one-shot "reject final once then let it through" that a second final bypasses.
What to check first: the loop guard / final-rejection logic must reject EVERY
final while a condition holds, not just the first; and a fix "for" instruction
compliance usually needs a code-level invariant (e.g. `did_code_change`) rather
than a prompt rule.

Do NOT treat a single rejection as sufficient — the agent can emit a second,
barely-different final that sails through.

## Memory / Qdrant silent degradation

Embedding-search and reflexion store failures historically degraded silently
(returned `[]` or fell through) rather than surfacing. If retrieval looks wrong:
check the collection name actually exists (`/memories`/`/tools/cache` counts
404'd when a hardcoded collection like `upwork_learning` wasn't the live sharded
name), and that `_ensure_collection` is not permanently set after a transient
startup failure. A memory query that always 404s usually means the code is
targeting a collection Qdrant doesn't have — list live collections first.

## When to escalate

These patterns cover recurring infrastructure/agent-loop classes. If the failure
is novel (no section here matches), read the actual call site and the live state
before hypothesizing — do not force-fit it to a known pattern. The changelog in
AGENTS.md is the authoritative incident history; this file is only the distilled
"check X first" layer. When the live code and this pattern disagree, the live
code wins.
