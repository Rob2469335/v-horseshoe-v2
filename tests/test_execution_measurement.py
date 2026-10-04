"""Step 9 regression tests for the typed mutation measurement.

`measure_mutation` reads the ATIF execution boundary (filesystem
`arguments.operation` + observation `ok`) and returns two coarse tri-state
booleans. It must never guess False when the evidence is absent or indirect, and
it must never return anything but booleans (no path/argument/content).
"""
from __future__ import annotations

import pytest

from runtime_v2.api.execution_measurement import measure_mutation, pair_observation_ok


def _call(name: str, op: str | None = None, ok: bool | None = None) -> dict:
    call: dict = {"function_name": name}
    if op is not None:
        call["arguments"] = {"operation": op}
    if ok is not None:
        call["extra"] = {"observation_ok": ok}
    return call


class TestDirectFilesystemMutation:
    def test_write_attempted(self):
        assert measure_mutation([_call("filesystem", "write", True)]) == (True, True)

    def test_patch_attempted(self):
        assert measure_mutation([_call("filesystem", "patch", True)]) == (True, True)

    def test_create_attempted(self):
        assert measure_mutation([_call("filesystem", "create", True)]) == (True, True)

    def test_read_is_not_a_mutation(self):
        assert measure_mutation([_call("filesystem", "read", True)]) == (False, False)

    def test_glob_is_not_a_mutation(self):
        assert measure_mutation([_call("filesystem", "glob", True)]) == (False, False)

    def test_failed_mutation_attempted_but_not_succeeded(self):
        assert measure_mutation([_call("filesystem", "write", False)]) == (True, False)

    def test_mutation_success_unknown_without_observation(self):
        assert measure_mutation([_call("filesystem", "patch")]) == (True, None)

    def test_mixed_mutation_outcomes_succeeded_true(self):
        calls = [_call("filesystem", "write", False), _call("filesystem", "patch", True)]
        assert measure_mutation(calls) == (True, True)


class TestIndirectAndUnknown:
    def test_indirect_tool_is_unknown(self):
        assert measure_mutation([_call("sandbox_repl")]) == (None, None)

    def test_indirect_with_reads_is_unknown_not_false(self):
        calls = [_call("filesystem", "read", True), _call("sandbox_repl")]
        assert measure_mutation(calls) == (None, None)

    def test_mcp_is_unknown(self):
        assert measure_mutation([_call("mcp")]) == (None, None)

    def test_git_is_unknown(self):
        assert measure_mutation([_call("git")]) == (None, None)

    def test_unrecognized_tool_is_unknown(self):
        assert measure_mutation([_call("mystery_tool")]) == (None, None)

    def test_unrecognized_filesystem_op_is_unknown(self):
        assert measure_mutation([_call("filesystem", "frobnicate", True)]) == (None, None)

    def test_no_calls_is_unknown(self):
        assert measure_mutation([]) == (None, None)

    def test_malformed_entries_fail_closed(self):
        assert measure_mutation([None, 42, "x", {}]) == (None, None)

    def test_known_non_mutating_tools_are_false(self):
        calls = [_call("web_search"), _call("lsp"), _call("filesystem", "read", True)]
        assert measure_mutation(calls) == (False, False)


class TestOperationAliases:
    """The alias sets must track the executor, not a sample (SOTA: fail closed
    on ambiguous resolution, but recognize the operations the runtime accepts)."""

    @pytest.mark.parametrize("op", [
        "write", "write_file", "create", "create_file", "save", "put",
        "patch", "edit", "edit_file", "update", "modify", "modify_file",
        "replace", "replace_file_content",
    ])
    def test_mutation_alias_detected(self, op):
        assert measure_mutation([_call("filesystem", op, True)]) == (True, True)

    @pytest.mark.parametrize("op", [
        "read", "read_file", "read_graph", "view", "cat", "get", "glob",
        "list", "ls", "tree", "walk", "grep", "search", "find", "stat",
    ])
    def test_read_alias_is_not_a_mutation(self, op):
        assert measure_mutation([_call("filesystem", op, True)]) == (False, False)


class TestObservationPairing:
    """tool_call_id <-> source_call_id correlation (ATSC/AER-style identity)."""

    def test_pairs_each_call_to_its_own_result(self):
        step = {
            "step_id": 7,
            "tool_calls": [
                {"tool_call_id": "r:1", "function_name": "filesystem",
                 "arguments": {"operation": "write"}},
                {"tool_call_id": "r:2", "function_name": "filesystem",
                 "arguments": {"operation": "read"}},
            ],
            "observation": {"results": [
                {"source_call_id": "r:1", "extra": {"ok": True}},
                {"source_call_id": "r:2", "extra": {"ok": False}},
            ]},
        }
        paired = pair_observation_ok(step)
        assert paired[0]["extra"]["observation_ok"] is True
        assert paired[1]["extra"]["observation_ok"] is False
        assert paired[0]["extra"]["step_id"] == 7

    def test_multi_call_cannot_mispair(self):
        step = {
            "step_id": 1,
            "tool_calls": [{"tool_call_id": "r:1", "function_name": "filesystem",
                            "arguments": {"operation": "write"}}],
            "observation": {"results": [{"source_call_id": "r:2", "extra": {"ok": True}}]},
        }
        paired = pair_observation_ok(step)
        assert paired[0]["extra"]["observation_ok"] is None  # unmatched -> UNKNOWN
        assert measure_mutation(paired) == (True, None)

    def test_unkeyed_single_result_applies(self):
        step = {
            "step_id": 1,
            "tool_calls": [{"function_name": "filesystem", "arguments": {"operation": "patch"}}],
            "observation": {"results": [{"extra": {"ok": True}}]},
        }
        assert measure_mutation(pair_observation_ok(step)) == (True, True)

    def test_missing_observation_is_unknown(self):
        step = {"step_id": 1, "tool_calls": [
            {"tool_call_id": "r:1", "function_name": "filesystem",
             "arguments": {"operation": "write"}}]}
        assert measure_mutation(pair_observation_ok(step)) == (True, None)


class TestMeasurementCarriesNoPayload:
    def test_returns_only_booleans_even_with_hostile_payload(self):
        call = {
            "function_name": "filesystem",
            "arguments": {
                "operation": "write",
                "path": "/secret/solution.py",
                "content": "PATCH -- known_solution_symbol task-specific marker",
                "old": "known_solution_symbol",
                "command": "rm -rf /",
            },
            "extra": {"observation_ok": True},
        }
        result = measure_mutation([call])
        assert result == (True, True)
        for item in result:
            assert item is True or item is False or item is None
