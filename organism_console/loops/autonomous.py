import sys
import os
import subprocess
import re
from pathlib import Path
from rich.panel import Panel
from rich.rule import Rule
from rich.prompt import Confirm

from organism_console.config import PROJECT_ROOT
from organism_console.api_client import call_api
from organism_console.command_registry import run_syntax_checks
from organism_console.ui.live_stream import stream_prompt
from organism_console._commands_opencode import (
    snapshot_worktree,
    restore_snapshot,
)

_healing_loop = None


def _apply_snapshot_bytes(snap: dict, root: Path = PROJECT_ROOT) -> list[str]:
    """Re-apply a snapshot_worktree()-shaped dict's BYTE content in place.

    restore_snapshot() removes agent-created untracked files and rewrites
    tracked bytes — but it does NOT write back untracked file content (its
    /undo purpose is removal). The goal loop needs the reverse for the
    baseline-eval: after running tests on the restored baseline, the agent's
    changes — including any new (untracked) file's content — must be put back
    EXACTLY. This writes the snapshot's tracked and untracked_content bytes.
    Returns the relpaths written.
    """
    written: list[str] = []
    for rel, content in (snap.get("tracked") or {}).items():
        p = root / rel
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)
            written.append(rel)
        except OSError:
            pass
    for rel, content in (snap.get("untracked_content") or {}).items():
        p = root / rel
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)
            written.append(rel)
        except OSError:
            pass
    return written


def _is_placeholder_final(text: str) -> bool:
    """True when an agent's final message is a bare success placeholder with no
    substantive content (e.g. 'Task completed.', 'Done.', 'All done.') — the
    sign that the agent short-circuited instead of doing the requested work."""
    t = re.sub(r"\s+", " ", str(text or "")).strip().lower()
    if not t:
        return True
    t = t.strip("[]()\"'`*_ ").strip()
    # Allow a short substantive answer, but flag bare completion-verbs-only.
    short = len(t) <= 40
    verbs = (
        "task completed",
        "task complete",
        "done",
        "all done",
        "completed",
        "finished",
        "success",
        "goal achieved",
        "task done",
        "complete.",
        # French
        "terminé",
        "fini",
        "accompli",
        "complété",
        # German
        "fertig",
        "erledigt",
        "abgeschlossen",
        # Spanish
        "listo",
        "hecho",
        "completado",
        "terminado",
        # Italian
        "fatto",
        "completato",
        "finito",
        # Portuguese
        "feito",
        "concluído",
        "terminado",
    )
    if not short:
        return False
    return any(
        t == v or t.startswith(v + ".") or t.startswith(v + "!") or t == v + "."
        for v in verbs
    )


def run_test_suite(
    goal_text: str = "",
    baseline: set[str] | None = None,
    changed: set[str] | None = None,
) -> tuple[bool | None, str]:
    """Run pytest against test targets derived from files modified by the agent
    during THIS attempt (not the whole uncommitted working tree — pre-existing
    local edits must not derail the verification or feed the coordinator bogus
    failure logs). `changed` is the set of paths added after `baseline`.

    Returns:
      (True, log)  — pytest ran and passed.
      (False, log) — pytest ran and failed.
      (None, log)  — no test targets cover the changed files. The caller MUST
                     fall back to LLM goal verification instead of declaring
                     success — returning True here rubber-stamped every failed
                     run as "all tests passed" whenever the working tree was
                     dirty (which it almost always is).
    """
    test_targets = []
    m = re.search(r"tests/[a-zA-Z0-9_]+\.py", goal_text)
    if m:
        test_targets.append(m.group(0))

    changed = changed or set()
    if not test_targets and changed:
        for f in sorted(changed):
            if f.startswith("tests/") and f.endswith(".py"):
                test_targets.append(f)
                continue
            base = Path(f).stem
            if len(base) > 3 and base not in ("main", "__init__"):
                tests_dir = PROJECT_ROOT / "tests"
                for t_file in tests_dir.glob("test_*.py"):
                    if (
                        base in t_file.name
                        or t_file.name.replace("test_", "").replace(".py", "") in base
                    ):
                        test_targets.append(f"tests/{t_file.name}")

    test_targets = list(set(test_targets))
    if not test_targets:
        return (
            None,
            "No tests cover the files changed this attempt; falling back to LLM goal verification.",
        )

    cmd = [sys.executable, "-m", "pytest", "--tb=short"] + test_targets
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=120,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0:
            return True, result.stdout + "\n" + result.stderr

        # FLAKE-AWARE: a first-run failure is re-run once against only the
        # last-failed subset (--lf), scoped to the same targets. A passing
        # re-run means the failure was a flaky test, NOT a broken agent patch —
        # do not reject a sound patch on a transient failure (same semantics as
        # repair_engine._run_related_tests).
        retry_cmd = [
            sys.executable,
            "-m",
            "pytest",
            "--tb=short",
            "--lf",
        ] + test_targets
        retry_result = subprocess.run(
            retry_cmd,
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=120,
            encoding="utf-8",
            errors="replace",
        )
        combined = (
            result.stdout
            + "\n"
            + result.stderr
            + "\n--- last-failed re-run ---\n"
            + retry_result.stdout
            + "\n"
            + retry_result.stderr
        )
        if retry_result.returncode == 0:
            return (
                True,
                "[flaky] first run failed, last-failed re-run passed:\n" + combined,
            )
        return False, combined
    except subprocess.TimeoutExpired:
        return False, "Test execution timed out after 120 seconds."
    except Exception as e:
        return False, f"Failed to execute tests: {e}"


def _scan_changed_for_security(changed: set[str]) -> tuple[bool, str]:
    """Run the security gate over every `.py` file changed this attempt.

    An agent patch (especially one drafted from internet research) must not
    introduce a banned construct (exec/eval/subprocess/socket/ctypes/pty/shlex,
    destructive os calls) before the goal loop accepts it. Returns (ok, message);
    any violation message names the offending file + reason for the correction
    feedback loop.
    """
    from swarm_os.services.security_gate import SecurityGate, SecurityGateViolation

    violations = []
    for rel in sorted(changed or set()):
        if not rel.endswith(".py"):
            continue
        file_path = PROJECT_ROOT / rel
        if not file_path.exists():
            continue
        try:
            SecurityGate.scan_file(file_path)
        except SecurityGateViolation as e:
            violations.append(f"{rel}: {e}")
        except Exception as e:
            violations.append(f"{rel}: security scan error: {e}")
    if violations:
        return False, "\n".join(violations)
    return True, ""


def _verify_goal_with_reviewer(goal: str, final_msg: str) -> tuple[bool, str]:
    """Ask the reviewer agent whether the goal was actually achieved.

    Fail-closed: if the reviewer call fails or is unreachable, the goal is
    treated as NOT verified — never a pass. The old behavior had a fail-open
    fallback (a failed reviewer call was reported as a pass)."""
    verify_prompt = (
        f"Did the agent successfully achieve this goal: '{goal}'? "
        f"The agent's final response was: '{final_msg}'. "
        f"Answer ONLY 'YES' or 'NO: <reason>'."
    )
    try:
        resp = call_api(
            "/generate", "POST", {"prompt": verify_prompt, "agent_id": "reviewer"}
        )
        if resp and resp.status_code == 200:
            data = resp.json()
            verdict = data.get("response", data.get("content", "")).strip()
            if verdict.startswith("YES"):
                return True, "Goal achieved without file modifications."
            return False, f"Goal verification failed: {verdict}"
        return False, "Goal verification unavailable (reviewer call failed)."
    except Exception as e:
        return False, f"Goal verification unavailable: {e}"


def _record_verification_reflexion(
    goal: str, component: str, trace_preview: str, final_msg: str, console=None
) -> None:
    """Event-driven reflexion: a goal-loop verification failure writes a
    ReflexionMemory rule immediately (deterministic ID -> deduped) so the
    learning loop closes on CLI goal runs too — not only agent-loop tool
    failures that RepairWatchman already consumes. Never raises: a failed
    store must never break the goal loop."""
    try:
        from swarm_os.services.reflection_loop import get_reflection_service
        import asyncio as _asyncio

        head = (
            " | ".join(s.strip()[:120] for s in (trace_preview or "").splitlines()[:5])
            or (final_msg or "")[:200]
        )
        reason = f"goal verification failed: {head[:400]}"

        async def _record():
            await get_reflection_service().store_reflexion(
                task=f"agent:{component} autonomous goal {goal[:140]}",
                action="verification_failed",
                failure_reason=reason,
                correction=(
                    "After editing files for a goal, run the related tests and confirm the "
                    "changed modules pass BEFORE calling final. A passing reviewer verdict "
                    "requires the goal's deliverables to exist in the working tree."
                ),
                do_not_repeat=f"agent:{component} must verify its own changes (tests/syntax) before final.",
                component=component,
                confidence=0.7,
            )

        try:
            # Keep a strong reference so the event loop's GC cannot silently
            # reap this fire-and-forget reflexion task mid-await, and surface
            # any exception it raises instead of dropping it.
            _record_task = _asyncio.get_running_loop().create_task(_record())

            def _consume(_t: _asyncio.Task) -> None:
                if not _t.cancelled() and _t.exception() and console is not None:
                    console.print(f"[dim]reflexion task failed: {_t.exception()}[/dim]")

            _record_task.add_done_callback(_consume)
        except RuntimeError:
            _asyncio.run(_record())
    except Exception as exc:
        if console is not None:
            console.print(f"[dim]reflexion store skipped: {exc}[/dim]")


SYSTEM_FAILURE_MARKERS = (
    "[System: max turns reached]",
    "Healing failed.",
    "Task aborted after",
    "Loop aborted",
    # The agent loop's L1 3-strike abort final ("Task FAILED: <agent> could not
    # produce a substantive, grounded final."). It was missing here, so BOTH the
    # goal loop and the CLI treated that failed run as a SUCCESS (the CLI even
    # rendered it in a green success panel).
    "Task FAILED",
)


def _is_system_failure_final(text: str) -> bool:
    """True when an agent's final message is a system-level termination
    (max turns reached / loop aborted / healing failed / LLM abort) rather
    than a real completion. These must be treated as FAILED runs — previously
    the max-turns final was yielded as a normal `final` chunk, so the
    verification loop verified a failed run and reported SUCCESS."""
    return any(m in str(text or "") for m in SYSTEM_FAILURE_MARKERS)


def draft_plan_first(goal: str, cmd_ctx) -> str:
    console = cmd_ctx.console
    console.print("[dim]Drafting structured implementation plan...[/dim]")
    prompt = f"""
    You are an elite software architect. Create a structured markdown Implementation Plan for the objective: "{goal}".

    Structure your plan as follows:
    # Goal Description
    - Summary of changes
    ## Proposed Changes
    - Specify the exact files to modify and what changes to make in each.
    ## Verification Plan
    - Tests to run and manual verification steps.

    Return ONLY valid markdown text.
    """
    try:
        resp = call_api(
            "/generate",
            "POST",
            {"prompt": prompt, "agent_id": cmd_ctx.state.active_agent},
        )
        if resp and resp.status_code == 200:
            data = resp.json()
            return data.get("response", data.get("content", "")).strip()
    except Exception as e:
        console.print(f"[red]Error calling generator: {e}[/red]")
    return ""


def draft_task_list(plan_text: str, cmd_ctx) -> str:
    console = cmd_ctx.console
    state = cmd_ctx.state

    console.print("[dim]Drafting task checklist...[/dim]")
    prompt = f"""
    Based on the following Implementation Plan, generate a checklist of specific tasks.
    Each item must start with `- [ ]`.
    
    Plan:
    {plan_text}
    
    Return ONLY the list of items starting with `- [ ]`.
    """

    try:
        resp = call_api(
            "/generate", "POST", {"prompt": prompt, "agent_id": state.active_agent}
        )
        if resp and resp.status_code == 200:
            data = resp.json()
            return data.get("response", data.get("content", "")).strip()
    except Exception:
        pass
    return "- [ ] Implement proposed changes\n- [ ] Verify execution"


def run_autonomous_goal_loop(
    goal: str, cmd_ctx, *, force_readonly: bool = False
) -> str:
    """Run the autonomous goal loop. Returns the final assistant message content
    (the agent's last `final` output) so callers like `/upgrade` can capture a
    research proposal before deciding whether to apply changes.

    `force_readonly=True` deterministically treats the goal as read-only even if
    the objective text contains write-intent keywords (used by the research phase
    of `/upgrade`, whose proposal text must never be misclassified as a write
    goal).
    """
    console = cmd_ctx.console
    state = cmd_ctx.state

    console.print()
    console.print(
        Rule("[bold magenta]🤖 Swarm OS Autonomous Verification Loop[/bold magenta]")
    )
    console.print(f"🎯 [bold]Goal[/bold]: [cyan]{goal}[/cyan]")
    entry_agent = getattr(state, "entry_agent", None) or "coordinator"
    state.active_agent = entry_agent
    state.delegation_chain = [entry_agent]
    state.save()
    console.print(f"👥 [bold]Initial Agent[/bold]: [cyan]{entry_agent}[/cyan]")

    READ_ONLY_KEYWORDS = [
        "analyze",
        "analyse",
        "audit",
        "scan",
        "search",
        "inspect",
        "review",
        "list",
        "read",
        "show",
        "display",
        "print",
        "what is",
        "where is",
        "how many",
        "explain",
        "summarize",
        "describe",
        "browse",
        "look up",
        "look for",
        "find",
        "check",
    ]
    WRITE_KEYWORDS = [
        "fix",
        "implement",
        "add",
        "change",
        "refactor",
        "write",
        "modify",
        "update",
        "create",
        "delete",
        "remove",
        "patch",
        "edit",
        "generate",
        "produce",
        "construct",
        "make",
        "alter",
        "adjust",
        "apply",
        "fixes",
    ]

    goal_lower = goal.lower()
    has_read_only = any(
        re.search(r"\b" + re.escape(kw) + r"\b", goal_lower)
        for kw in READ_ONLY_KEYWORDS
    )
    has_write = any(
        re.search(r"\b" + re.escape(kw) + r"\b", goal_lower) for kw in WRITE_KEYWORDS
    )
    # A goal is READ-ONLY when it asks for research/analysis and has NO write
    # intent — regardless of whether it's multi-step. "analyze the codebase and
    # search the internet for improvements" is a research task whose deliverable
    # is a REPORT, not file changes. The old `and not is_multi_step` clause was
    # wrong: the multi-step keywords (analyze/search/review/audit/scan/inspect)
    # overlap the read-only keywords, so every multi-step research goal was
    # forced into the fix-verification loop, which demands file changes and
    # failed with "No file changes detected". Write intent (fix/implement/
    # patch/write/edit/...) is the ONLY thing that moves a goal into the
    # fix pipeline — and that is already captured by has_write below.
    is_readonly = force_readonly or (has_read_only and not has_write)

    if is_readonly:
        console.print(
            "[dim]Detected read-only goal — running as a single tool call, skipping verification loop.[/dim]"
        )
        history = stream_prompt(cmd_ctx.state, entry_agent, goal, list(state.history))
        content = ""
        if history and isinstance(history[-1], dict):
            content = str(history[-1].get("content", ""))
        try:
            state.last_goal_result = {
                "content": content,
                "files_changed": [],
                "passed": bool(content),
            }
        except Exception:
            pass
        return content
    console.print()

    plan_first = len(goal) > 200 and Confirm.ask(
        "[bold cyan]Goal is complex. Would you like to draft an implementation plan first?[/bold cyan]",
        default=False,
    )

    if plan_first:
        docs_dir = PROJECT_ROOT / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)
        plan_file = docs_dir / "implementation_plan.md"
        task_file = docs_dir / "task.md"

        while True:
            plan_text = draft_plan_first(goal, cmd_ctx)
            if not plan_text:
                console.print(
                    "[red]Failed to generate plan. Falling back to immediate execution.[/red]"
                )
                break

            console.print()
            console.print(
                Panel(
                    plan_text,
                    title="📋 [bold green]Implementation Plan Proposal[/bold green]",
                    border_style="green",
                )
            )
            console.print()

            if Confirm.ask(
                "[bold cyan]Approve this plan and proceed to task creation?[/bold cyan]"
            ):
                plan_file.write_text(plan_text, encoding="utf-8")
                console.print(
                    f"[green]✓ Saved implementation plan to {plan_file}[/green]"
                )

                task_text = draft_task_list(plan_text, cmd_ctx)
                task_file.write_text(task_text, encoding="utf-8")
                console.print(f"[green]✓ Saved task checklist to {task_file}[/green]")
                break
            else:
                refinement = console.input(
                    "[yellow]Provide feedback/refinements to regenerate the plan: [/yellow]"
                ).strip()
                goal = f"{goal} (Feedback: {refinement})"

    current_prompt = f"Goal: {goal}\n\n"
    if plan_first and "plan_text" in locals() and "task_text" in locals():
        current_prompt += (
            f"Implementation Plan:\n{plan_text}\n\nTask Checklist:\n{task_text}\n\n"
        )

    if entry_agent == "coordinator":
        current_prompt += (
            "*** CRITICAL INSTRUCTION ***\n"
            "You are the `coordinator` agent. Your ONLY job is to act as a router. You MUST NOT attempt to solve this goal yourself.\n"
            "You MUST immediately use the `delegate` tool to route this task.\n"
            "- For simple or well-defined coding tasks, delegate DIRECTLY to the `coder` agent to save time.\n"
            "- For internet research, analyzing the state of the art, or finding external information, delegate DIRECTLY to the `researcher` agent.\n"
            "- For complex, multi-file architectures requiring deep thought, delegate to the `planner`.\n"
            "Just output the JSON `delegate` payload and nothing else."
        )
    else:
        current_prompt += "Please audit, refactor, and fix the codebase to achieve this goal using your tools. Ensure syntax correctness and that all tests pass.\n"

    # Start autonomous goals with a clean slate to prevent repeating previous goal outputs
    history = []
    max_attempts = int(os.environ.get("SWARM_MAX_ATTEMPTS", "5"))

    def _git_status_paths() -> set[str]:
        """Snapshot of current git working-tree paths (modified/untracked)."""
        try:
            out = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True,
                text=True,
                cwd=PROJECT_ROOT,
                timeout=5,
                encoding="utf-8",
                errors="replace",
            )
            paths = set()
            for line in out.stdout.splitlines():
                if len(line) >= 4:
                    paths.add(line[3:])
            return paths
        except Exception:
            return set()

    for attempt in range(1, max_attempts + 1):
        console.print(Rule(f"Attempt {attempt}/{max_attempts}", style="magenta dim"))

        # Baseline the tree BEFORE the agent runs so verification only counts
        # files the agent actually touched THIS attempt — not the pre-existing
        # uncommitted work in the tree (which was derailing the whole loop).
        attempt_baseline = _git_status_paths()
        attempt_base_snap = snapshot_worktree()
        state.delegation_chain = [entry_agent]
        state.save()

        global _healing_loop
        if _healing_loop is None:
            from swarm_os.healing.healing_loop import HealingLoop as _HL

            _healing_loop = _HL()
        heal_result = _healing_loop.tick()
        if heal_result.get("status") == "healing_decision":
            decision = heal_result.get("decision", {})
            mode = decision.get("mode", "unknown")
            component = heal_result.get("component", "unknown")
            console.print(
                f"[bold yellow]⚕ Self-healing:[/bold yellow] issue detected in [cyan]{component}[/cyan] (mode: {mode})"
            )
            reasoning = decision.get("reasoning", "")
            if mode in ("auto_execute", "sandbox_first"):
                from swarm_os.healing.recovery_engine import RecoveryEngine

                recovery = RecoveryEngine()
                symptom = heal_result.get("all_signals", [{}])[0] or {
                    "component": component
                }
                from swarm_os.healing.failure_detector import run_coro_sync

                result = run_coro_sync(recovery.recover(symptom))
                if result and result.get("ok"):
                    console.print(
                        f"[bold green]✓ Auto-recovered:[/bold green] {result.get('action')}"
                    )
                else:
                    console.print(
                        f"[bold red]✗ Auto-recovery failed:[/bold red] {(result or {}).get('error', 'no recovery result')}"
                    )
                try:
                    _healing_loop.finalize(decision, result)
                except Exception:
                    pass
            elif mode == "approval_required":
                console.print(
                    Panel(
                        f"[bold yellow]🔧 Approval Required:[/bold yellow] [{component}]\n[dim]{reasoning}[/dim]",
                        border_style="yellow",
                    )
                )
                cmd_ctx.state.last_error = f"Healing approval required on {component}"
                cmd_ctx.state.save()
            elif mode == "reject":
                console.print(
                    Panel(
                        f"[bold red]⛔ Rejected by Governor:[/bold red] [{component}] - [dim]{reasoning}[/dim]",
                        border_style="red",
                    )
                )
                cmd_ctx.state.last_error = f"Healing rejected for {component}"
                cmd_ctx.state.save()

        agent_id = getattr(state, "active_agent", "coordinator")
        console.print(f"  🤖 Running agent: [bold cyan]{agent_id}[/bold cyan]")
        history = stream_prompt(cmd_ctx.state, agent_id, current_prompt, history)

        passed = False
        logs = ""

        if getattr(cmd_ctx.state, "last_stream_status", "") != "completed":
            passed = False
            logs = "Agent stream interrupted prematurely (e.g., hit max tokens, fell into a repetition loop, or the backend crashed).\nNo changes were finalized."
            console.print(
                "[bold red]✗ Agent execution failed (Interrupted).[/bold red]"
            )
            break
        else:
            final_msg = ""
            if history and isinstance(history[-1], dict):
                final_msg = history[-1].get("content", "")

            # Empty/placeholder finals: an agent that returns a bare "Task
            # completed." / "Done." with no substantive content, no file changes,
            # and no tool activity did NOT actually do the goal. Fail fast with a
            # concrete correction instead of burning a full review cycle (the
            # reviewer would just reject it and the loop retries on the same
            # empty final).
            try:
                changed_this_attempt = _git_status_paths() - attempt_baseline
            except Exception:
                changed_this_attempt = set()
            placeholder = (
                not changed_this_attempt
                and final_msg
                and _is_placeholder_final(final_msg)
            )

            if "Unable to determine next action" in final_msg:
                passed = False
                logs = (
                    "Final Action:\n"
                    + final_msg
                    + "\nAgent could not determine next action. It likely encountered a critical backend error or model routing failure."
                )
                console.print(
                    "[bold red]✗ Agent execution failed (Routing Fallback).[/bold red]"
                )
                break
            elif _is_system_failure_final(final_msg):
                # The max-turns / loop-abort / healing-failed finals are yielded
                # as normal `final` chunks, so last_stream_status reads
                # "completed" and the old gate verified a FAILED run as success.
                passed = False
                logs = (
                    "Final Action:\n"
                    + final_msg
                    + "\nAgent stream hit a system-level termination "
                    "(max turns reached / loop aborted / LLM failure). No goal was completed."
                )
                console.print(
                    "[bold red]✗ Agent execution failed (System termination).[/bold red]"
                )
                break
            elif placeholder:
                passed = False
                logs = (
                    "Agent returned a placeholder final (e.g. 'Task completed.') without making any "
                    "file changes or doing the requested analysis/research. The goal required real work "
                    "(read files, run tools, search the web). Re-run with the ORIGINAL goal and do not "
                    f"short-circuit to a bare success.\nFinal: {final_msg[:200]}"
                )
                console.print(
                    "[bold red]✗ Agent returned an empty/placeholder final.[/bold red]"
                )
            else:
                console.print("[dim]Running fast syntax checks...[/dim]")
                syntax_passed, syntax_error_msg = run_syntax_checks(PROJECT_ROOT)

            if (
                not placeholder
                and not syntax_passed
                and "Unable to determine next action" not in final_msg
                and not _is_system_failure_final(final_msg)
            ):
                passed = False
                logs = f"Syntax Error detected in modified files:\n\n{syntax_error_msg}"
                console.print("[bold red]✗ Fast Syntax Check Failed.[/bold red]")
            elif not placeholder and syntax_passed:
                # Check if any files were actually modified during this attempt
                if not changed_this_attempt:
                    if has_write:
                        # A write-intent goal (fix/implement/patch/write/apply...)
                        # that produced ZERO file changes did not do its job. The
                        # reviewer reports on the FINAL RESPONSE, not on the
                        # deliverable — it approved a research report for an
                        # edit goal ("No file changes detected. Verifying goal...
                        # SUCCESS"). Fail closed and tell the agent to actually
                        # make the edit, rather than rubber-stamping a report.
                        console.print(
                            "[dim]Write-intent goal produced no file changes —"
                            " not passing on reviewer alone.[/dim]"
                        )
                        passed = False
                        logs = (
                            "The goal requires EDITING code (write/fix/implement/"
                            "patch/apply), but this attempt changed no files. Final "
                            "response was a report, not the deliverable. Re-run with "
                            "the ORIGINAL goal and use action=filesystem "
                            "(operation=write or operation=patch) to actually apply "
                            "the fix before calling final.\n"
                            f"Final: {final_msg[:200]}"
                        )
                        console.print(
                            "[bold red]✗ Write-intent goal with no file changes.[/bold red]"
                        )
                    else:
                        console.print(
                            "[dim]No file changes detected. Verifying goal...[/dim]"
                        )
                        passed, logs = _verify_goal_with_reviewer(goal, final_msg)
                else:
                    console.print(
                        "[dim]Running security gate on changed files...[/dim]"
                    )
                    sec_ok, sec_logs = _scan_changed_for_security(changed_this_attempt)
                    if not sec_ok:
                        passed = False
                        logs = (
                            "Security Gate rejected a modified file (banned "
                            "construct — exec/eval/subprocess/socket/ctypes/pty/"
                            "shlex or destructive os call):\n\n" + sec_logs
                        )
                        console.print(
                            "[bold red]✗ Security Gate rejected a modified file.[/bold red]"
                        )
                    else:
                        console.print("[dim]Running test verification suite...[/dim]")
                        test_passed, test_logs = run_test_suite(
                            goal,
                            baseline=attempt_baseline,
                            changed=changed_this_attempt,
                        )
                        if test_passed is None:
                            # No tests cover this attempt's changes — do NOT pass on
                            # that alone. Ask the reviewer whether the goal was met.
                            console.print(
                                "[dim]No tests cover the changed files. Verifying goal...[/dim]"
                            )
                            passed, logs = _verify_goal_with_reviewer(goal, final_msg)
                        else:
                            passed, logs = test_passed, test_logs

        if passed:
            console.print()
            console.print(
                Panel(
                    "[bold green]✓ SUCCESS: Goal fully achieved and verified! All tests passed.[/bold green]",
                    border_style="green",
                )
            )
            break
        else:
            # Evaluate if the failure is a regression by checking the baseline state
            is_regression = False
            if changed_this_attempt:
                console.print(
                    "[dim]Evaluating baseline to determine if failure is a regression...[/dim]"
                )
                # Evaluate the TRUE pre-agent baseline using the project's
                # snapshot/restore primitives (untracked-safe), NOT `git stash`:
                # `git stash push -- <untracked>` refuses the whole stash with
                # rc=1 (pathspec did not match), silently no-ops, and the later
                # `git clean -f -- <untracked>` deletes the agent's new file —
                # a data-loss path when the patch was only a new file.
                agent_snap = snapshot_worktree()
                restore_snapshot(attempt_base_snap)
                # restore_snapshot only reverts files captured in the snapshot
                # (those differing from HEAD at baseline). A tracked file that
                # was HEAD-clean at baseline then modified by the agent must be
                # reverted via `git checkout HEAD` so the baseline test runs on
                # the TRUE pre-agent code.
                _base_tracked = set((attempt_base_snap.get("tracked") or {}).keys())
                _clean_modified = [
                    p
                    for p in changed_this_attempt
                    if p not in _base_tracked and (PROJECT_ROOT / p).is_file()
                ]
                if _clean_modified:
                    subprocess.run(
                        ["git", "checkout", "HEAD", "--"] + _clean_modified,
                        cwd=PROJECT_ROOT,
                        capture_output=True,
                    )
                try:
                    # Run the exact same tests on the true baseline
                    baseline_test_passed, _ = run_test_suite(
                        goal,
                        baseline=attempt_baseline,
                        changed=changed_this_attempt,
                    )
                finally:
                    # Put the agent's work back BEFORE deciding/reverting —
                    # tracked bytes AND untracked file content (restore_snapshot
                    # cannot write untracked content back).
                    _apply_snapshot_bytes(agent_snap)

                if baseline_test_passed is True:
                    is_regression = True

            if is_regression:
                console.print(
                    Panel(
                        "[bold red]✗ <RATCHET_GUARDRAIL_TRIGGERED> Agent patch regressed passing baseline tests! Automatically reverting...[/bold red]",
                        border_style="red",
                    )
                )
                # Diff-scoped revert to the pre-agent baseline: writes back the
                # tracked baseline bytes and removes ONLY agent-created untracked
                # files in scope. Never `git clean -f` (would delete the agent's
                # new files); never touches pre-existing untracked work.
                restore_snapshot(attempt_base_snap, scope=list(changed_this_attempt))
                # The snapshot only captures files that DIFFERED from HEAD at
                # baseline. A tracked file that was HEAD-clean at baseline then
                # modified by the agent is NOT in the snapshot — restore it via
                # `git checkout HEAD` (safe: a tracked file, no untracked data).
                base_tracked = set((attempt_base_snap.get("tracked") or {}).keys())
                clean_modified = [
                    p
                    for p in changed_this_attempt
                    if p not in base_tracked and Path(p).is_file()
                ]
                if clean_modified:
                    subprocess.run(
                        ["git", "checkout", "HEAD", "--"] + clean_modified,
                        cwd=PROJECT_ROOT,
                        capture_output=True,
                    )
                console.print(
                    "[green]✓ Diverging patch automatically reverted.[/green]"
                )

            failures = []
            for line in logs.splitlines():
                if (
                    line.startswith("E   ")
                    or "FAIL" in line
                    or "AssertionError" in line
                    or "Syntax Error" in line
                    or "File:" in line
                    or "Error:" in line
                ):
                    failures.append(line)

            trace_preview = "\n".join(failures[:50])
            if not trace_preview:
                trace_preview = "\n".join(logs.splitlines()[-30:])

            # Event-driven reflexion: record a ReflexionMemory rule for this
            # verification failure immediately (deterministic ID -> deduped) so
            # the closed learning loop sees CLI goal failures, not just agent
            # tool failures.
            try:
                _record_verification_reflexion(
                    goal, entry_agent, trace_preview, final_msg, console
                )
            except Exception:
                pass

            console.print(
                f"[bold red]✗ Verification Failed on Attempt {attempt}.[/bold red]"
            )
            if attempt == max_attempts:
                console.print(
                    Panel(
                        f"[bold red]✗ FAILURE: Max attempts ({max_attempts}) reached. Tests/Checks are still failing.[/bold red]",
                        border_style="red",
                    )
                )
                if sys.stdin.isatty():
                    try:
                        git_status = subprocess.run(
                            ["git", "status", "--porcelain"],
                            capture_output=True,
                            text=True,
                            cwd=PROJECT_ROOT,
                            timeout=5,
                            encoding="utf-8",
                            errors="replace",
                        )
                        has_changes = bool(git_status.stdout.strip())
                    except Exception:
                        has_changes = False
                    if has_changes and Confirm.ask(
                        "[bold yellow]Do you want to run `git stash` to revert the broken changes and safely exit?[/bold yellow]"
                    ):
                        res = subprocess.run(
                            ["git", "stash"],
                            cwd=PROJECT_ROOT,
                            capture_output=True,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                        )
                        if res.returncode == 0:
                            console.print(
                                "[green]Working directory reverted via `git stash`.[/green]"
                            )
                        else:
                            console.print(
                                f"[red]git stash failed: {res.stderr.strip()}[/red]"
                            )
                    elif not has_changes:
                        console.print(
                            "[dim]No file changes detected — nothing to stash. Working directory is clean.[/dim]"
                        )
                break

            console.print(
                "[yellow]Feeding back failure logs to agent context for correction...[/yellow]"
            )
            current_prompt = (
                f"ORIGINAL GOAL: {goal}\n\n"
                f"<EPHEMERAL_MESSAGE>\n"
                f"The verification checks failed with the following traceback/logs:\n\n"
                f"```\n{trace_preview}\n```\n\n"
                f"Please analyze these errors, modify the code using your capabilities, and verify syntax to fix them.\n"
            )

            if entry_agent == "coordinator":
                current_prompt += (
                    "CRITICAL: If you are the `coordinator`, you MUST delegate this to the `debugger`, `coder`, `researcher`, or `planner`.\n"
                    "Just output the JSON `delegate` payload and nothing else."
                )

            current_prompt += "</EPHEMERAL_MESSAGE>\n\n"

    # Return the final assistant message content (all exit paths: success, max
    # attempts, failure). Callers that only need the loop's side effects ignore it.
    if history and isinstance(history[-1], dict):
        content = str(history[-1].get("content", ""))
        # Record the machine-readable goal outcome so single-command `--json`
        # mode can report `ok:true` with real content + files instead of the
        # empty result it used to print for /goal (cmd_goal returned None and
        # handle_line fed nothing into run_agentic). `changed_this_attempt` is
        # the LAST attempt's baseline delta; `passed` holds this run's verdict.
        try:
            state.last_goal_result = {
                "content": content,
                "files_changed": sorted(changed_this_attempt or ()),
                "passed": bool(passed),
            }
        except Exception:
            pass
        return content
    return ""
