from rich.rule import Rule
from rich.panel import Panel
from rich.prompt import Confirm

def run_debate_loop(goal: str, cmd_ctx):
    console = cmd_ctx.console
    state = cmd_ctx.state
    
    console.print()
    console.print(Rule("[bold magenta]🐝 Swarm OS Collaborative Agent Debate[/bold magenta]"))
    console.print(f"🎯 [bold]Goal[/bold]: [cyan]{goal}[/cyan]")
    console.print()
    
    console.print("[bold yellow]Phase 1: Planner Proposal[/bold yellow]")
    prompt1 = f"Please draft a detailed implementation proposal to achieve this goal: {goal}"
    cmd_ctx.run_prompt_with_agent("planner", prompt1)
    proposal = state.history[-1].get("content", "") if state.history else ""
    
    console.print("\n[bold yellow]Phase 2: Executor Implementation Strategy[/bold yellow]")
    prompt2 = f"Based on the following proposal, draft the specific code changes and commands needed:\n\n{proposal}"
    cmd_ctx.run_prompt_with_agent("executor", prompt2)
    execution_plan = state.history[-1].get("content", "") if state.history else ""

    console.print("\n[bold yellow]Phase 3: Reviewer Critique[/bold yellow]")
    prompt3 = f"Please audit the following implementation proposal and code plan for bugs, edge cases, and design flaws:\n\nPROPOSAL:\n{proposal}\n\nEXECUTION PLAN:\n{execution_plan}"
    cmd_ctx.run_prompt_with_agent("reviewer", prompt3)
    critique = state.history[-1].get("content", "") if state.history else ""
    
    console.print("\n[bold yellow]Phase 4: Coordinator Synthesis[/bold yellow]")
    prompt4 = (
        f"You are the coordinator. Review the proposal, execution plan, and the critic's critique, resolve any disagreements, "
        f"and output a final corrected implementation plan.\n\n"
        f"PROPOSAL:\n{proposal}\n\n"
        f"EXECUTION PLAN:\n{execution_plan}\n\n"
        f"CRITIQUE:\n{critique}"
    )
    cmd_ctx.run_prompt_with_agent("coordinator", prompt4)
    
    console.print()
    console.print(Panel(
        "[bold green]✓ Swarm debate complete. Final plan recorded in history. Run /plan show to inspect.[/bold green]",
        border_style="green"
    ))
    
    if Confirm.ask("[bold cyan]Approve this synthesized plan for execution by the Executor agent?[/bold cyan]"):
        state.active_agent = "executor"
        state.save()
        cmd_ctx.run_goal_loop("Execute the synthesized debate plan")
