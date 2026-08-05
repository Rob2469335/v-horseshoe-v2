import sys
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

_healing_loop = None

def run_test_suite(goal_text: str = "", baseline: set[str] | None = None) -> tuple[bool, str]:
    """Run pytest against test targets derived from files modified by the agent
    during THIS attempt (not the whole uncommitted working tree — pre-existing
    local edits must not derail the verification or feed the coordinator bogus
    failure logs). `baseline` is the set of modified paths captured before the
    attempt; only paths added after it are considered agent work."""
    test_targets = []
    m = re.search(r"tests/[a-zA-Z0-9_]+\.py", goal_text)
    if m:
        test_targets.append(m.group(0))
    
    if not test_targets:
        try:
            git_diff = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True,
                text=True,
                cwd=PROJECT_ROOT,
                timeout=5,
                encoding="utf-8",
                errors="replace"
            )
            modified_files = []
            for line in git_diff.stdout.splitlines():
                if len(line) >= 4:
                    modified_files.append(line[3:])
            for f in modified_files:
                if baseline is not None and f in baseline:
                    continue
                if f.startswith("tests/") and f.endswith(".py"):
                    test_targets.append(f)
                else:
                    base = Path(f).stem
                    if len(base) > 3 and base not in ("main", "__init__"):
                        tests_dir = PROJECT_ROOT / "tests"
                        for t_file in tests_dir.glob("test_*.py"):
                            if base in t_file.name or t_file.name.replace("test_", "").replace(".py", "") in base:
                                test_targets.append(f"tests/{t_file.name}")
        except Exception:
            pass

    test_targets = list(set(test_targets))

    cmd = [sys.executable, "-m", "pytest", "--tb=short"]
    if test_targets:
        cmd.extend(test_targets)
    else:
        # Check if files were actually modified during this turn
        try:
            git_diff = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=5, encoding="utf-8", errors="replace")
            if not git_diff.stdout.strip():
                return False, "No files were modified. The agent did not generate or change any code to fulfill the goal."
        except Exception:
            pass
        return True, "No specific tests found for modifications. Skipping test suite."
        
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=120,
            encoding="utf-8",
            errors="replace"
        )
        passed = result.returncode == 0
        return passed, result.stdout + "\n" + result.stderr
    except subprocess.TimeoutExpired:
        return False, "Test execution timed out after 120 seconds."
    except Exception as e:
        return False, f"Failed to execute tests: {e}"

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
        resp = call_api("/generate", "POST", {"prompt": prompt, "agent_id": cmd_ctx.state.active_agent})
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
        resp = call_api("/generate", "POST", {"prompt": prompt, "agent_id": state.active_agent})
        if resp and resp.status_code == 200:
            data = resp.json()
            return data.get("response", data.get("content", "")).strip()
    except Exception:
        pass
    return "- [ ] Implement proposed changes\n- [ ] Verify execution"

def run_autonomous_goal_loop(goal: str, cmd_ctx):
    console = cmd_ctx.console
    state = cmd_ctx.state
    
    console.print()
    console.print(Rule("[bold magenta]🤖 Swarm OS Autonomous Verification Loop[/bold magenta]"))
    console.print(f"🎯 [bold]Goal[/bold]: [cyan]{goal}[/cyan]")
    entry_agent = getattr(state, "entry_agent", None) or "coordinator"
    state.active_agent = entry_agent
    state.delegation_chain = [entry_agent]
    state.save()
    console.print(f"👥 [bold]Initial Agent[/bold]: [cyan]{entry_agent}[/cyan]")

    READ_ONLY_KEYWORDS = [
        "analyze", "analyse", "audit", "scan", "search", "inspect", "review",
        "list", "read", "show", "display",
        "print", "what is", "where is", "how many", "explain", "summarize", "describe",
        "browse", "look up", "look for", "find", "check"
    ]
    WRITE_KEYWORDS = [
        "fix", "implement", "add", "change", "refactor", "write", "modify", "update",
        "create", "delete", "remove", "patch", "edit", "generate", "produce",
        "construct", "make", "alter", "adjust"
    ]
    MULTI_STEP_KEYWORDS = [
        "analyze", "analyse", "inspect", "search", "compare", "upgrade", "review", "audit", "scan"
    ]

    goal_lower = goal.lower()
    has_read_only = any(re.search(r"\b" + re.escape(kw) + r"\b", goal_lower) for kw in READ_ONLY_KEYWORDS)
    has_write = any(re.search(r"\b" + re.escape(kw) + r"\b", goal_lower) for kw in WRITE_KEYWORDS)
    is_multi_step = any(re.search(r"\b" + re.escape(kw) + r"\b", goal_lower) for kw in MULTI_STEP_KEYWORDS)
    is_readonly = has_read_only and not has_write and not is_multi_step

    if is_readonly:
        console.print("[dim]Detected read-only goal — running as a single tool call, skipping verification loop.[/dim]")
        stream_prompt(cmd_ctx.state, entry_agent, goal, list(state.history))
        return
    console.print()
    
    plan_first = len(goal) > 200 and Confirm.ask("[bold cyan]Goal is complex. Would you like to draft an implementation plan first?[/bold cyan]", default=False)
    
    if plan_first:
        docs_dir = PROJECT_ROOT / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)
        plan_file = docs_dir / "implementation_plan.md"
        task_file = docs_dir / "task.md"
        
        while True:
            plan_text = draft_plan_first(goal, cmd_ctx)
            if not plan_text:
                console.print("[red]Failed to generate plan. Falling back to immediate execution.[/red]")
                break
                
            console.print()
            console.print(Panel(plan_text, title="📋 [bold green]Implementation Plan Proposal[/bold green]", border_style="green"))
            console.print()
            
            if Confirm.ask("[bold cyan]Approve this plan and proceed to task creation?[/bold cyan]"):
                plan_file.write_text(plan_text, encoding="utf-8")
                console.print(f"[green]✓ Saved implementation plan to {plan_file}[/green]")
                
                task_text = draft_task_list(plan_text, cmd_ctx)
                task_file.write_text(task_text, encoding="utf-8")
                console.print(f"[green]✓ Saved task checklist to {task_file}[/green]")
                break
            else:
                refinement = console.input("[yellow]Provide feedback/refinements to regenerate the plan: [/yellow]").strip()
                goal = f"{goal} (Feedback: {refinement})"
    
    current_prompt = f"Goal: {goal}\n\n"
    if plan_first and 'plan_text' in locals() and 'task_text' in locals():
        current_prompt += f"Implementation Plan:\n{plan_text}\n\nTask Checklist:\n{task_text}\n\n"
    
    if entry_agent == "coordinator":
        current_prompt += (
            "*** CRITICAL INSTRUCTION ***\n"
            "You are the `coordinator` agent. Your ONLY job is to act as a router. You MUST NOT attempt to solve this goal yourself.\n"
            "You MUST immediately use the `delegate` tool to route this task.\n"
            "- For simple or well-defined coding tasks, delegate DIRECTLY to the `coder` agent to save time.\n"
            "- For complex, multi-file architectures requiring deep thought, delegate to the `planner`.\n"
            "Just output the JSON `delegate` payload and nothing else."
        )
    else:
        current_prompt += (
            "Please audit, refactor, and fix the codebase to achieve this goal using your tools. Ensure syntax correctness and that all tests pass.\n"
        )
    
    # Start autonomous goals with a clean slate to prevent repeating previous goal outputs
    history = []
    max_attempts = 5
    baseline_passed = False
    
    def _git_status_paths() -> set[str]:
        """Snapshot of current git working-tree paths (modified/untracked)."""
        try:
            out = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=5,
                encoding="utf-8", errors="replace"
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
        
        global _healing_loop
        if _healing_loop is None:
            from swarm_os.healing.healing_loop import HealingLoop as _HL
            _healing_loop = _HL()
        heal_result = _healing_loop.tick()
        if heal_result.get("status") == "healing_decision":
            decision = heal_result.get("decision", {})
            mode = decision.get("mode", "unknown")
            component = heal_result.get("component", "unknown")
            console.print(f"[bold yellow]⚕ Self-healing:[/bold yellow] issue detected in [cyan]{component}[/cyan] (mode: {mode})")
            reasoning = decision.get("reasoning", "")
            if mode in ("auto_execute", "sandbox_first"):
                from swarm_os.healing.recovery_engine import RecoveryEngine
                recovery = RecoveryEngine()
                symptom = heal_result.get("all_signals", [{}])[0] or {"component": component}
                from swarm_os.healing.failure_detector import run_coro_sync
                result = run_coro_sync(recovery.recover(symptom))
                if result and result.get("ok"):
                    console.print(f"[bold green]✓ Auto-recovered:[/bold green] {result.get('action')}")
                else:
                    console.print(f"[bold red]✗ Auto-recovery failed:[/bold red] {(result or {}).get('error', 'no recovery result')}")
                try:
                    _healing_loop.finalize(decision, result)
                except Exception:
                    pass
            elif mode == "approval_required":
                console.print(Panel(f"[bold yellow]🔧 Approval Required:[/bold yellow] [{component}]\n[dim]{reasoning}[/dim]", border_style="yellow"))
                cmd_ctx.state.last_error = f"Healing approval required on {component}"
                cmd_ctx.state.save()
            elif mode == "reject":
                console.print(Panel(f"[bold red]⛔ Rejected by Governor:[/bold red] [{component}] - [dim]{reasoning}[/dim]", border_style="red"))
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
            console.print("[bold red]✗ Agent execution failed (Interrupted).[/bold red]")
            break
        else:
            final_msg = ""
            if history and isinstance(history[-1], dict):
                final_msg = history[-1].get("content", "")
            
            syntax_passed = False
            
            if "Unable to determine next action" in final_msg:
                passed = False
                logs = "Final Action:\n" + final_msg + "\nAgent could not determine next action. It likely encountered a critical backend error or model routing failure."
                console.print("[bold red]✗ Agent execution failed (Routing Fallback).[/bold red]")
                break
            else:
                console.print("[dim]Running fast syntax checks...[/dim]")
                syntax_passed, syntax_error_msg = run_syntax_checks(PROJECT_ROOT)
                
            if not syntax_passed and "Unable to determine next action" not in final_msg:
                passed = False
                logs = f"Syntax Error detected in modified files:\n\n{syntax_error_msg}"
                console.print("[bold red]✗ Fast Syntax Check Failed.[/bold red]")
            elif syntax_passed:
                # Check if any files were actually modified during this attempt
                try:
                    changed_this_attempt = _git_status_paths() - attempt_baseline
                    if not changed_this_attempt:
                        console.print("[dim]No file changes detected. Verifying goal...[/dim]")
                        verify_prompt = f"Did the agent successfully achieve this goal: '{goal}'? The agent's final response was: '{final_msg}'. Answer ONLY 'YES' or 'NO: <reason>'."
                        resp = call_api("/generate", "POST", {"prompt": verify_prompt, "agent_id": "reviewer"})
                        if resp and resp.status_code == 200:
                            data = resp.json()
                            verdict = data.get("response", data.get("content", "")).strip()
                            if verdict.startswith("YES"):
                                passed = True
                                logs = "Goal achieved without file modifications."
                            else:
                                passed = False
                                logs = f"Goal verification failed: {verdict}"
                        else:
                            passed = True
                            logs = "No files were modified. Verification unavailable."
                    else:
                        console.print("[dim]Running test verification suite...[/dim]")
                        passed, logs = run_test_suite(goal, baseline=attempt_baseline)
                except Exception:
                    console.print("[dim]Running test verification suite...[/dim]")
                    passed, logs = run_test_suite(goal, baseline=attempt_baseline)
        
        if passed:
            console.print()
            console.print(Panel(
                "[bold green]✓ SUCCESS: Goal fully achieved and verified! All tests passed.[/bold green]",
                border_style="green"
            ))
            break
        else:
            if baseline_passed and not passed:
                console.print(Panel(
                    "[bold red]✗ <RATCHET_GUARDRAIL_TRIGGERED> Agent patch regressed passing baseline tests! Automatically reverting via `git stash`...[/bold red]",
                    border_style="red"
                ))
                res = subprocess.run(["git", "stash"], cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
                if res.returncode == 0:
                    console.print("[green]✓ Diverging patch automatically reverted.[/green]")
            failures = []
            for line in logs.splitlines():
                if line.startswith("E   ") or "FAIL" in line or "AssertionError" in line or "Syntax Error" in line or "File:" in line or "Error:" in line:
                    failures.append(line)
            
            trace_preview = "\n".join(failures[:50])
            if not trace_preview:
                trace_preview = "\n".join(logs.splitlines()[-30:])
                
            console.print(f"[bold red]✗ Verification Failed on Attempt {attempt}.[/bold red]")
            if attempt == max_attempts:
                console.print(Panel(
                    f"[bold red]✗ FAILURE: Max attempts ({max_attempts}) reached. Tests/Checks are still failing.[/bold red]",
                    border_style="red"
                ))
                if sys.stdin.isatty():
                    try:
                        git_status = subprocess.run(
                            ["git", "status", "--porcelain"],
                            capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=5,
                            encoding="utf-8", errors="replace"
                        )
                        has_changes = bool(git_status.stdout.strip())
                    except Exception:
                        has_changes = False
                    if has_changes and Confirm.ask("[bold yellow]Do you want to run `git stash` to revert the broken changes and safely exit?[/bold yellow]"):
                        res = subprocess.run(["git", "stash"], cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
                        if res.returncode == 0:
                            console.print("[green]Working directory reverted via `git stash`.[/green]")
                        else:
                            console.print(f"[red]git stash failed: {res.stderr.strip()}[/red]")
                    elif not has_changes:
                        console.print("[dim]No file changes detected — nothing to stash. Working directory is clean.[/dim]")
                break
                
            console.print("[yellow]Feeding back failure logs to agent context for correction...[/yellow]")
            current_prompt = (
                f"ORIGINAL GOAL: {goal}\n\n"
                f"<EPHEMERAL_MESSAGE>\n"
                f"The verification checks failed with the following traceback/logs:\n\n"
                f"```\n{trace_preview}\n```\n\n"
                f"Please analyze these errors, modify the code using your capabilities, and verify syntax to fix them.\n"
            )
            
            if entry_agent == "coordinator":
                current_prompt += (
                    "CRITICAL: If you are the `coordinator`, you MUST delegate this to the `debugger` or `coder`.\n"
                    "Just output the JSON `delegate` payload and nothing else."
                )
                
            current_prompt += "</EPHEMERAL_MESSAGE>\n\n"
