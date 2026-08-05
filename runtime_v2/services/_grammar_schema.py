"""JSON Schema for constrained grammar-based tool-decision decoding (local path).

This schema is a direct transcription of TOOL_CALL_SCHEMA in
runtime_v2/services/_llm_parser.py (captured 2026-08-02). The two MUST be kept
in sync — see tests/test_grammar_decode.py::test_schema_remains_synced, which
fails loudly if this module's action enum or additionalProperties diverge from
the parser's live schema.

Purpose: when SWARM_GRAMMAR_DECODE=1, the local llama.cpp generation path
(port 8080) is constrained via response_format json_schema so the model can
only emit syntactically valid tool-decision JSON, instead of relying entirely
on _llm_parser.py's post-hoc salvage logic. Grammar guarantees syntax only,
not semantic completeness (e.g. {"action":"final"} with no "response" is
accepted) — that conditional gap is deliberately deferred.
"""
from __future__ import annotations

TOOL_DECISION_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "thought": {"type": "string"},
        "action": {
            "type": "string",
            "enum": [
                "delegate", "web_search", "filesystem", "sandbox_repl",
                "vscode_automation", "semantic_search", "remember", "ask_user",
                "lsp", "mcp", "mcp_register", "self_heal", "final"
            ]
        },
        "target_agent": {"type": "string"},
        "server_name": {"type": "string"},
        "task": {"type": "string"},
        "query": {"type": "string"},
        "operation": {"type": "string"},
        "path": {"type": "string"},
        "content": {"type": "string"},
        "old": {"type": "string"},
        "new": {"type": "string"},
        "language": {"type": "string"},
        "code": {"type": "string"},
        "command": {"type": "string"},
        "args": {"type": "array", "items": {"type": "string"}},
        "response": {"type": "string"},
        "fact": {"type": "string"},
        "category": {"type": "string"},
        "question": {"type": "string"}
    },
    "required": ["action"]
}