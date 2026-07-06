import sys
import subprocess
import re
import ast
import requests
from pathlib import Path
from rich.panel import Panel
from rich.rule import Rule
from rich.prompt import Confirm

from organism_console.config import PROJECT_ROOT
from organism_console.api_client import call_api
from organism_console.ui.live_stream import stream_prompt
from swarm_os.healing.healing_loop import HealingLoop

_healing_loop = HealingLoop()
def run_syntax_checks() -> tuple[bool, str]:
    try:
        git_diff = subprocess.run(
            ["git", "diff", "--name-only"],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=5
        )
        if git_diff.returncode == 0:
            modified_files = [line.strip() for line in git_diff.stdout.splitlines() if line.strip()]
            for f in modified_files:
                file_path = PROJECT_ROOT / f
                if file_path.suffix == ".py" and file_path.exists():
                    try:
                        content = file_path.read_text(encoding="utf-8", errors="ignore")
                        ast.parse(content, filename=str(file_path))
                    except SyntaxError as exc:
                        lines = content.splitlines()
                        err_line = exc.lineno
                        context_lines = []
                        if err_line:
                            start = max(0, err_line - 4)
                            end = min(len(lines), err_line + 3)
                            for idx in range(start, end):
                                prefix = ">>> " if idx + 1 == err_line else "    "
                                context_lines.append(f"{prefix}{idx+1}: {lines[idx]}")
                        context_str = "\\n".join(context_lines)
                        return False, f"File: {f}\\nError: {exc.msg} at line {exc.lineno}\\nCode Context:\\n```python\\n{context_str}\\n```"
    except Exception as e:
        return False, f"Syntax checks crashed: {e}"
    return True, ""

def run_test_suite(goal_text: str = "") -> tuple[bool, str]:
    test_targets = []
    m = re.search(r"tests/[a-zA-Z0-9_]+\.py", goal_text)
    if m:
        test_targets.append(m.group(0))
    
    if not test_targets:
        try:
            git_diff = subprocess.run(
                ["git", "diff", "--name-only"],
                capture_output=True,
                text=True,
                cwd=PROJECT_ROOT,
                timeout=5
            )
            if git_diff.returncode == 0:
                modified_files = [line.strip() for line in git_diff.stdout.splitlines() if line.strip()]
                for f in modified_files:
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
        return True, "No specific tests found for modifications. Skipping test suite."
        
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=30
        )
        passed = result.returncode == 0
        return passed, result.stdout + "\\n" + result.stderr
    except subprocess.TimeoutExpired:
        return False, "Test execution timed out after 30 seconds."
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
    return "- [ ] Implement proposed changes\\n- [ ] Verify execution"

def run_autonomous_goal_loop(goal: str, cmd_ctx):
    console = cmd_ctx.console
    state = cmd_ctx.state
    
    console.print()
    console.print(Rule("[bold magenta]🤖 Swarm OS Autonomous Verification Loop[/bold magenta]"))
    console.print(f"🎯 [bold]Goal[/bold]: [cyan]{goal}[/cyan]")
    entry_agent = "coordinator"
    state.active_agent = entry_agent
    state.delegation_chain = [entry_agent]
    state.save()
    console.print(f"👥 [bold]Initial Agent[/bold]: [cyan]{entry_agent}[/cyan]")

    heal_result = _healing_loop.tick()
    if heal_result.get("status") == "healing_decision":
        decision = heal_result.get("decision", {})
        mode = decision.get("mode", "unknown")
        component = heal_result.get("component", "unknown")
        console.print(f"[bold yellow]⚕ Self-healing:[/bold yellow] issue detected in [cyan]{component}[/cyan] (mode: {mode})")
        if mode == "auto":
            import asyncio
            from swarm_os.healing.recovery_engine import RecoveryEngine
            recovery = RecoveryEngine()
            symptom = heal_result.get("all_signals", [{}])[0]
            result = asyncio.run(recovery.recover(symptom))
            if result.get("ok"):
                console.print(f"[bold green]✓ Auto-recovered:[/bold green] {result.get('action')}")
            else:
                console.print(f"[bold red]✗ Recovery attempt failed:[/bold red] {result.get('error', result.get('reason', 'unknown'))}")
        elif mode == "approval_required":
            console.print("[dim]Recovery requires approval — continuing goal, but component may be degraded.[/dim]")

    READONLY_PATTERNS = r"\b(list|show|display|read|find|search|check|view|print|what|where|how many)\b"
    CODE_ACTION_PATTERNS = r"\b(fix|refactor|implement|add|modify|update|create|write|delete|remove|change)\b"
    is_readonly = bool(re.search(READONLY_PATTERNS, goal, re.IGNORECASE)) and not re.search(CODE_ACTION_PATTERNS, goal, re.IGNORECASE)

    if is_readonly:
        console.print("[dim]Detected read-only goal — running as a single tool call, skipping verification loop.[/dim]")
        stream_prompt(cmd_ctx.state, entry_agent, goal, list(state.history))
        return
    console.print()
    
    plan_first = False
    
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
    
    current_prompt += (
        "Please audit, refactor, and fix the codebase to achieve this goal using your tools. Ensure syntax correctness and that all tests pass.\n\n"
        "*** CRITICAL INSTRUCTION ***\n"
        "If you are the `coordinator` agent, you MUST NOT refuse this task or output plain text. You MUST immediately use the `delegate` tool to hand this off to the `planner` or `researcher`.\n"
        "DO NOT say 'The verification process is failing' or 'To proceed manually...'\n"
        "Just output the JSON `delegate` payload."
    )
    
    history = list(state.history)
    max_attempts = 5
    
    for attempt in range(1, max_attempts + 1):
        console.print(Rule(f"Attempt {attempt}/{max_attempts}", style="magenta dim"))
        
        history = stream_prompt(cmd_ctx.state, entry_agent, current_prompt, history)
        
        if getattr(cmd_ctx.state, "last_stream_status", "") != "completed":
            passed = False
            logs = "Agent stream interrupted prematurely (e.g., hit max tokens, fell into a repetition loop, or the backend crashed).\\nNo changes were finalized."
            console.print("[bold red]✗ Agent execution failed (Interrupted).[/bold red]")
        else:
            console.print("[dim]Running fast syntax checks...[/dim]")
            syntax_passed, syntax_error_msg = run_syntax_checks()
            if not syntax_passed:
                passed = False
                logs = f"Syntax Error detected in modified files:\\n\\n{syntax_error_msg}"
                console.print("[bold red]✗ Fast Syntax Check Failed.[/bold red]")
            else:
                console.print("[dim]Running test verification suite...[/dim]")
                passed, logs = run_test_suite(goal)
        
        if passed:
            console.print()
            console.print(Panel(
                "[bold green]✓ SUCCESS: Goal fully achieved and verified! All tests passed.[/bold green]",
                border_style="green"
            ))
            break
        else:
            failures = []
            for line in logs.splitlines():
                if line.startswith("E   ") or "FAIL" in line or "AssertionError" in line or "Syntax Error" in line or "File:" in line or "Error:" in line:
                    failures.append(line)
            
            trace_preview = "\\n".join(failures[:20])
            if not trace_preview:
                trace_preview = "\\n".join(logs.splitlines()[-15:])
                
            console.print(f"[bold red]✗ Verification Failed on Attempt {attempt}.[/bold red]")
            if attempt == max_attempts:
                console.print(Panel(
                    f"[bold red]✗ FAILURE: Max attempts ({max_attempts}) reached. Tests/Checks are still failing.[/bold red]",
                    border_style="red"
                ))
                if Confirm.ask("[bold yellow]Do you want to run `git stash` to revert the broken changes and safely exit?[/bold yellow]"):
                    subprocess.run(["git", "stash"], cwd=PROJECT_ROOT)
                    console.print("[green]Working directory reverted.[/green]")
                break
                
            console.print("[yellow]Feeding back failure logs to agent context for correction...[/yellow]")
            current_prompt = (
                f"<EPHEMERAL_MESSAGE>\n"
                f"The verification checks failed with the following traceback/logs:\n\n"
                f"```\n{trace_preview}\n```\n\n"
                f"Please analyze these errors, modify the code using your capabilities, and verify syntax to fix them.\n"
                f"CRITICAL: If you are the `coordinator`, you MUST delegate this to the `planner` or `researcher`.\n"
                f"</EPHEMERAL_MESSAGE>"
            )


