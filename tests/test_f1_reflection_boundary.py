"""Regression tests for the Experiment J reflection-daemon evidence boundary.

Two distinct properties are pinned here.

1. EVIDENCE INTEGRITY (the scientific firewall)
   `run_reflection` calls `PromptRepairer.process_failure` WITHOUT a `task_id`
   (reflection_loop.py:961-966). PromptRepairer rejects untagged events
   (`prompt_repairer.py:1230-1231` -> "ignored: untagged_event"), so a reflection
   pass can never create a candidate, never append evidence, and therefore can
   never influence candidate eligibility, MIN_EVIDENCE_RUNS / MIN_EVIDENCE_TASKS,
   or any promotion path. This is asserted behaviourally, not by inspection.

2. NON-INTERFERENCE (measurement validity)
   The daemon was UNCONDITIONAL: it ran on a 120 s first-delay / 600 s cadence
   even under the Experiment J governance baseline SWARM_AUTONOMY=0, issuing its
   own LLM call against the same local model endpoint a governed observation is
   measuring, and injecting unattributed OBSERVED_FAILURE records into the audit
   log. It is now gated by SWARM_AUTONOMY, matching the watch-loop precedent at
   main.py:388.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from swarm_os.services import prompt_repairer as pr
from swarm_os.services import reflection_loop as rl

REPO = Path(__file__).resolve().parents[1]

# Shape taken from the real diary entry that drove the observed audit noise:
# an error, a task description, and NO run_id / component / agent / fix_class.
UNTAGGED_DIARY_ENTRY = {
    "ts": "2026-10-02T00:00:00Z",
    "task": "Write a Python function that retries an async HTTP call.",
    "content_preview": "",
    "error": "[WinError 10061] No connection could be made because the target "
             "machine actively refused it.",
}


@pytest.fixture
def seeded_diary(tmp_path, monkeypatch):
    """Point reflection at a temp diary holding one untagged failure."""
    diary = tmp_path / "organism_diary.jsonl"
    diary.write_text(json.dumps(UNTAGGED_DIARY_ENTRY) + "\n", encoding="utf-8")
    monkeypatch.setattr(rl, "DIARY_PATH", diary)
    return diary


@pytest.fixture
def isolated_repairer(isolate_prompt_repairer_store, monkeypatch):
    """A PromptRepairer bound to the isolated test store, not production."""
    monkeypatch.setattr(pr, "_repairer_instance", None)
    return pr.get_prompt_repairer()


# --------------------------------------------------------------------------
# 1. Evidence integrity
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reflection_cannot_create_learning_state(
    seeded_diary, isolated_repairer, monkeypatch
):
    """A full reflection pass over an untagged failure creates NOTHING."""
    # Stub only the LLM distill; the hand-off to PromptRepairer is the real one.
    monkeypatch.setattr(
        rl,
        "_distill",
        AsyncMock(
            return_value=(
                "<failure_summary>connection refused</failure_summary>"
                "<root_cause>service down</root_cause>"
                "<next_attempt_rules>check the port</next_attempt_rules>"
                "<do_not_repeat>retry blindly</do_not_repeat>"
            )
        ),
    )

    await rl.run_reflection()

    # The distilled rule WAS produced, and still NOTHING became learning state:
    # no candidate was created, so no evidence_runs / evidence_tasks can exist.
    assert isolated_repairer._candidates == {}, (
        "reflection created candidate(s): "
        f"{sorted(isolated_repairer._candidates)}"
    )


@pytest.mark.asyncio
async def test_untagged_process_failure_is_rejected(
    seeded_diary, isolate_prompt_repairer_store, monkeypatch
):
    """Directly pin the gate: no task_id => ignored, and no state mutation."""
    monkeypatch.setattr(pr, "_repairer_instance", None)
    repairer = pr.get_prompt_repairer()

    result = repairer.process_failure(
        run_id="11111111-2222-3333-4444-555555555555",
        component="unknown",
        failure_reason="[WinError 10061] connection refused",
        hypothesized_action="check the port before retrying",
        # task_id deliberately omitted -> ""
    )

    assert result == "ignored: untagged_event"
    assert repairer._candidates == {}


@pytest.mark.asyncio
async def test_reflection_never_supplies_a_task_id():
    """Source-level pin: the hand-off must not grow a task_id argument."""
    src = (REPO / "swarm_os" / "services" / "reflection_loop.py").read_text(
        encoding="utf-8"
    )
    call = src.split("repairer.process_failure(", 1)[1].split(")", 1)[0]
    assert "task_id" not in call, (
        "reflection must not pass task_id; untagged events are the reason the "
        "daemon cannot reach learning evidence"
    )


# --------------------------------------------------------------------------
# 2. Non-interference gate
# --------------------------------------------------------------------------

def test_reflection_daemon_is_gated_on_swarm_autonomy():
    """The daemon must honour the same governance flag as the watch-loop."""
    src = (REPO / "swarm_os" / "app" / "main.py").read_text(encoding="utf-8")
    block = src.split("[STEP", 1)[0]  # lifespan lives above STEP markers
    assert "_reflection_daemon" in block
    gate_line = 'environ.get("SWARM_AUTONOMY", "1")'
    assert gate_line in block, (
        "the reflection daemon is unconditional again; it must be gated by "
        "SWARM_AUTONOMY like the watch-loop"
    )
    # The gate must actually wrap the daemon creation, not merely appear.
    gate_idx = block.index(gate_line)
    create_idx = block.index("asyncio.create_task(_reflection_daemon())")
    assert gate_idx < create_idx, "the gate does not precede daemon creation"


def test_learning_harnesses_export_swarm_autonomy_zero():
    """Both governed harnesses already set the flag; the gate is therefore live."""
    for rel in ("qwen_train/run_repair_task.py", "qwen_train/f1_infra.py"):
        src = (REPO / rel).read_text(encoding="utf-8")
        # Accepted spellings: os.environ assignment, single-quoted assignment, or
        # an entry in an env dict handed to the child backend.
        assert any(
            form in src
            for form in (
                'SWARM_AUTONOMY"] = "0"',
                "SWARM_AUTONOMY'] = '0'",
                "SWARM_AUTONOMY']='0'",
                '"SWARM_AUTONOMY": "0"',
            )
        ), f"{rel} does not export SWARM_AUTONOMY=0"