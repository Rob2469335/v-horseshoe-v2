import re
import json
import time
import logging
from rich.live import Live
from rich.table import Table
from rich.text import Text
from rich.panel import Panel
from rich.rule import Rule
from rich.tree import Tree
from rich.markup import escape
from rich.markdown import Markdown

from organism_console.api_client import call_api
from organism_console.ui.banner import get_system_stats, estimate_tokens
from organism_console.renderer import render_step_micro_ui, render_trace_panel, render_tool_execution
from organism_console.token_tracker import record_chunk

log = logging.getLogger("zenith_cli")

_AGENT_PERF: dict = {}  # {agent_id: {"total": float, "count": int, "last": float}}
_AGENT_PERF_MAX = 256  # BUG FIX: cap to prevent unbounded memory growth

def update_token_metrics(ctx, prompt, history, output_content, model):
    input_tokens = estimate_tokens(prompt + json.dumps(history))
    output_tokens = estimate_tokens(output_content)
    ctx.total_input_tokens += input_tokens
    ctx.total_output_tokens += output_tokens
    model_name = (model or "unknown").lower()
    is_cloud = "cloud" in model_name or "groq" in model_name or "openrouter" in model_name
    if is_cloud or getattr(ctx, "last_provider", "ollama") != "ollama":
        ctx.cloud_input_tokens += input_tokens
        ctx.cloud_output_tokens += output_tokens

RE_TOOL_CALL = re.compile(r'<tool_call name="([^"]+)">')
RE_TOPIC_UPDATE = re.compile(r'<topic_update title="(.*?)" summary="(.*?)"')
RE_INTENT = re.compile(r'<strategic_intent>(.*?)</strategic_intent>', re.DOTALL)
RE_PLAN_CLEAN = re.compile(r"<plan>.*?(?:</plan>|$)", re.DOTALL)
RE_INTENT_CLEAN = re.compile(r"<strategic_intent>.*?(?:</strategic_intent>|$)", re.DOTALL)
RE_TOPIC_CLEAN = re.compile(r"<topic_update.*?>")
RE_TOOL_CLEAN = re.compile(r"<tool_call[^>]*>.*?(?:</tool_call>|$)", re.DOTALL)
RE_THINK_CLEAN = re.compile(r"<think>.*?(?:</think>|$)", re.DOTALL)
RE_PLAN_MATCH = re.compile(r"<plan>(.*?)</plan>", re.DOTALL)
RE_THINK_MATCH = re.compile(r"<think>(.*?)(?:</think>|$)", re.DOTALL)

def status_bar(ctx, agent, model, phase, ram_pct, tps: float = 0.0):
    stats = get_system_stats()
    phase_colors = {
        "thinking": "white",
        "planning": "yellow",
        "sensing": "cyan",
        "repair": "red",
        "swarm": "magenta",
        "resume": "blue",
        "ocular": "bright_cyan",
        "executing": "bright_green",
    }
    pc = phase_colors.get(phase, "white")
    tps_str = f" | tps:[bright_green]{tps:.1f}[/bright_green]" if tps > 0 else ""
    topic_str = str(ctx.current_topic)
    if len(topic_str) > 15:
        topic_str = topic_str[:12] + "..."
    agent_str = agent[:10]
    return (
        f"T:[bright_white]{escape(topic_str)}[/bright_white] | "
        f"A:[cyan]{agent_str}[/cyan] | "
        f"P:[{pc}]{phase}[/{pc}] | "
        f"RAM:[{stats['ram_color']}]{ram_pct:.0f}%[/{stats['ram_color']}]"
        f"{tps_str}"
    )

import asyncio
from organism_console.api_client import call_api_async_stream

async def _stream_prompt_async(ctx, agent_id, prompt, history):
    while True:
        stats = get_system_stats()
        if stats["ram_pct"] > 90:
            ctx.console.print("[bold red]WARNING:[/bold red] RAM critical, expect slower response.")

        client = None
        payload = {
            "agent_id": agent_id,
            "prompt": prompt,
            "history": history,
            "focus_file": getattr(ctx, "focus_file", None),
            "delegation_chain": getattr(ctx, "delegation_chain", [agent_id]),
        }
        try:
            client, resp = await call_api_async_stream(f"/agents/{agent_id}/step/stream", "POST", payload)
        except Exception as e:
            ctx.console.print(f"[bold red]ERROR:[/bold red] API call failed: {e}")
            return history

        if not resp:
            ctx.console.print("[bold red]ERROR:[/bold red] Backend unreachable.")
            # BUG FIX: Close the client even on early return to avoid leaking HTTP connections
            if client is not None:
                await client.aclose()
            return history

        full_content = ""
        model = "zenith-core"
        phase = "thinking"
        tool_calls = []
        handoffs_list = []
        start_time = time.time()
        _tokens_counted = False
        _char_count = 0

        ctx.console.print(Rule(title="[bold #ff00ea]COMM-LINK ESTABLISHED[/bold #ff00ea]", style="bold #00f0ff"))
        if not getattr(ctx, "delegation_chain", None):
            ctx.delegation_chain = [agent_id]
        ctx.last_stream_status = "interrupted"
        ctx.save()

        _stream_errored = False
        _ask_user_triggered = False

        with Live(console=ctx.console, refresh_per_second=15) as live:
            def safe_print(*args, **kwargs):
                live.console.print(*args, **kwargs)

            try:
                async for line in resp.aiter_lines():
                    if isinstance(line, bytes):
                        line = line.decode("utf-8", errors="replace")
                    if not line:
                        continue
                    if line.startswith("data: "):
                        line = line[6:]
                    if line == "[DONE]":
                        continue

                    try:
                        chunk = json.loads(line)
                        record_chunk(chunk)
                    except json.JSONDecodeError:
                        continue
                    chunk_type = chunk.get("type")

                    if "delegated_by" in chunk and "agent_id" in chunk:
                        parent = chunk["delegated_by"]
                        child = chunk["agent_id"]
                        if not getattr(ctx, "delegation_chain", None):
                            ctx.delegation_chain = [parent]
                        if ctx.delegation_chain[-1] != child:
                            ctx.delegation_chain.append(child)
                            ctx.save()

                    err_msg = chunk.get("error")
                    if not err_msg and isinstance(chunk.get("result"), dict) and "error" in chunk.get("result", {}):
                        err_msg = chunk["result"]["error"]

                    if err_msg:
                        err_agent = chunk.get("agent_id", ctx.active_agent)
                        err_tool = chunk.get("tool", "unknown")
                        safe_print(Panel(
                            f"[bold red]Error in {err_agent} (Tool: {err_tool}):[/bold red]\n{err_msg}",
                            border_style="red"
                        ))
                        continue

                    if chunk_type == "model_plan":
                        requested_role = chunk.get("requested_role", "unknown")
                        chain = chunk.get("model_chain", []) or []
                        ctx.delegation_chain = chain if chain else [agent_id]
                        ctx.save()

                        if ctx.trace_mode:
                            panel = render_trace_panel(
                                "Router Decision & Path Planning",
                                {"requested_role": requested_role, "delegation_path": " -> ".join(ctx.delegation_chain)},
                                "cyan"
                            )
                            safe_print(panel)
                        else:
                            safe_print(render_step_micro_ui("planning", f"Formulating plan for role: {requested_role}"))
                        continue

                    if chunk_type == "model_selected":
                        model = chunk.get("model", "unknown")
                        ctx.active_model = model
                        ctx.save()

                        if ctx.trace_mode:
                            panel = render_trace_panel(
                                "Model Selection",
                                {
                                    "model": model,
                                    "role": chunk.get("requested_role", "unknown"),
                                    "attempt": chunk.get("attempt", 1),
                                    "temperature": chunk.get("temperature", 0.7)
                                },
                                "green"
                            )
                            safe_print(panel)
                        else:
                            safe_print(render_step_micro_ui("model_selected", f"selected {model}"))
                        continue

                    if chunk_type == "model_escalation":
                        from_model = chunk.get("from_model")
                        reason = chunk.get("reason")
                        safe_print(
                            f"[bold yellow]  Fallback:[/bold yellow] [dim]{from_model}[/dim] timed out "
                            f"[bold yellow]→ Escalating to cloud[/bold yellow] [dim]({reason})[/dim]"
                        )
                        if ctx.trace_mode:
                            panel = render_trace_panel(
                                "Model Escalation (Fallback)",
                                {"from_model": from_model, "escalated_reason": reason, "status": "switching to secondary/cloud"},
                                "red"
                            )
                            safe_print(panel)
                        continue

                    if chunk_type == "agent_handoff":
                        from_a = chunk.get("from", agent_id)
                        to_a = chunk.get("to", "executor")
                        task = str(chunk.get("task", ""))[:80]

                        ctx.delegation_chain.append(to_a)
                        ctx.save()

                        handoffs_list.append({"from": from_a, "to": to_a, "task": task})
                        safe_print(render_step_micro_ui("swarm", f"{from_a} → {to_a}: {task}"))
                        continue

                    if chunk_type == "tool_call":
                        tool_name = chunk.get("tool") or chunk.get("name")
                        args_dict = chunk.get("arguments", {})
                        if args_dict:
                            safe_print(render_tool_execution(tool_name, args_dict))
                        else:
                            safe_print(render_step_micro_ui("tool_call", f"executing tool {tool_name}"))
                        continue

                    if chunk_type == "tool_result":
                        tool = chunk.get("tool")
                        payload = chunk.get("payload") or chunk.get("arguments")
                        if payload and isinstance(payload, dict):
                            safe_print(render_tool_execution(tool, payload))
                        elif ctx.trace_mode:
                            panel = render_trace_panel(
                                "Tool Execution Details",
                                {"tool": tool, "executing_model": chunk.get("model", "unknown")},
                                "yellow"
                            )
                            safe_print(panel)
                        else:
                            safe_print(render_step_micro_ui("tool_call", f"executing tool {tool}"))
                        continue

                    if ("content" in chunk or "thinking" in chunk or chunk_type == "ping") and chunk_type != "final":
                        piece = chunk.get("content") or chunk.get("thinking") or ""
                        model = chunk.get("model") or model
                        full_content += piece
                        _char_count += len(piece)

                        elapsed = time.time() - start_time
                        tps = (_char_count / 4) / elapsed if elapsed > 0.5 else 0.0

                        if "<plan>" in full_content and "</plan>" not in full_content:
                            phase = "planning"
                        elif "[Singularity:" in piece:
                            phase = "resume"
                        elif "Observation:" in piece:
                            phase = "sensing"
                        elif "[Self-Heal:" in piece:
                            phase = "repair"
                        elif "<tool_call" in piece:
                            phase = "executing"

                        tool_calls = RE_TOOL_CALL.findall(full_content)

                        topic_match = RE_TOPIC_UPDATE.search(full_content)
                        if topic_match:
                            ctx.current_topic = topic_match.group(1)
                            ctx.current_summary = topic_match.group(2)

                        intent_match = RE_INTENT.search(full_content)
                        if intent_match:
                            ctx.strategic_intent = intent_match.group(1).strip()

                        elapsed = time.time() - start_time
                        stats = get_system_stats()

                        display = RE_PLAN_CLEAN.sub("", full_content)
                        display = RE_INTENT_CLEAN.sub("", display)
                        display = RE_TOPIC_CLEAN.sub("", display)
                        display = RE_TOOL_CLEAN.sub("", display)
                        display = RE_THINK_CLEAN.sub("", display).strip()

                        layout = Table.grid(padding=(0, 0))
                        layout.add_column()
                        layout.add_row(Text.from_markup(status_bar(ctx, agent_id, model, phase, stats["ram_pct"], tps) + f" [dim]{elapsed:.1f}s[/dim]"))

                        if ctx.strategic_intent:
                            layout.add_row(Text.from_markup(f" [bold blue]intent[/bold blue]: [cyan]{ctx.strategic_intent}[/cyan]"))

                        if ctx.current_topic != "Nexus Initialization":
                            layout.add_row(Panel(escape(ctx.current_summary), title=f"[bold bright_white]{escape(ctx.current_topic)}[/bold bright_white]", border_style="blue dim"))

                        plan_match = RE_PLAN_MATCH.search(full_content)
                        if plan_match:
                            layout.add_row(Panel(plan_match.group(1).strip(), title="Plan", border_style="yellow dim"))

                        think_match = RE_THINK_MATCH.search(full_content)
                        if think_match:
                            think_content = think_match.group(1).strip()
                            if think_content:
                                layout.add_row(Panel(think_content, title="Thinking", border_style="dim white"))

                        if tool_calls:
                            layout.add_row(Text.from_markup(f"[dim]Tools:[/dim] {' '.join(f'[cyan]⚙ {t}[/cyan]' for t in tool_calls[-3:])}"))

                        if display:
                            display_lines = display.splitlines()
                            max_display_lines = 20
                            if len(display_lines) > max_display_lines:
                                # BUG FIX: Use real "\n" not escaped "\\n" which renders as literal backslash-n
                                display = "..." + "\n".join(display_lines[-max_display_lines:])
                            layout.add_row(Panel(escape(display), border_style="bright_blue dim", padding=(0, 1)))

                        if ctx.delegation_chain:
                            tree_obj = Tree("[bold magenta]🐝 Swarm Handoff Trace[/bold magenta]")
                            curr_node = tree_obj
                            for i, agent in enumerate(ctx.delegation_chain):
                                task_desc = ""
                                if i > 0:
                                    from_agent = ctx.delegation_chain[i-1]
                                    to_agent = ctx.delegation_chain[i]
                                    for h in handoffs_list:
                                        if h["from"] == from_agent and h["to"] == to_agent:
                                            task_desc = f" [dim]({h['task']})[/dim]"
                                            break
                                if i == len(ctx.delegation_chain) - 1:
                                    curr_node = curr_node.add(f"[bold green]▶ {agent}[/bold green] (active){task_desc}")
                                else:
                                    curr_node = curr_node.add(f"[cyan]✓ {agent}[/cyan]{task_desc}")
                            layout.add_row(Panel(tree_obj, border_style="magenta dim", title="[bold magenta]Live Handoff Trace[/bold magenta]"))

                        live.update(layout)
                        continue

                    if chunk_type == "final":
                        live.stop()
                        final_content = chunk.get("content", "")
                        ctx.last_provider = chunk.get("provider", "ollama")

                        if isinstance(final_content, dict):
                            ctx.console.print(Panel(json.dumps(final_content, indent=2), title="[bold #ff00ea]SWARM OS RESPONSE[/bold #ff00ea]", border_style="bold #00f0ff"))
                        elif final_content:
                            ctx.console.print(Panel(Markdown(str(final_content)), title="[bold #ff00ea]SWARM OS RESPONSE[/bold #ff00ea]", border_style="bold #00f0ff", padding=(1, 2)))

                        new_history = list(history)
                        if prompt:
                            new_history.append({"role": "user", "content": prompt})
                        new_history.append({"role": "assistant", "content": final_content or full_content})
                        ctx.history = new_history
                        ctx.history_pointer = len(ctx.history) - 1

                        elapsed_total = time.time() - start_time
                        update_token_metrics(ctx, prompt, history, final_content or full_content, model)

                        # BUG FIX: Cap _AGENT_PERF dict to prevent unbounded memory growth
                        if len(_AGENT_PERF) > _AGENT_PERF_MAX:
                            _AGENT_PERF.clear()
                        perf = _AGENT_PERF.setdefault(agent_id, {"total": 0.0, "count": 0, "last": 0.0})
                        perf["total"] += elapsed_total
                        perf["count"] += 1
                        perf["last"] = elapsed_total

                        ctx.last_stream_status = "completed"
                        ctx.save()
                        _tokens_counted = True
                        return new_history

                    if chunk_type == "ask_user":
                        ctx.last_stream_status = "completed"
                        question = chunk.get("question", "Input requested:")
                        options = chunk.get("options", [])

                        live.stop()
                        ctx.console.print()

                        if "APPROVAL REQUIRED" in question:
                            ctx.console.print(Panel(
                                Markdown(question),
                                title="🛡️  [bold yellow]Security Gate - Action Approval[/bold yellow]",
                                border_style="yellow",
                                padding=(1, 2)
                            ))
                        else:
                            ctx.console.print(Panel(
                                Markdown(question),
                                title="❓  [bold cyan]Agent Request[/bold cyan]",
                                border_style="cyan",
                                padding=(0, 1)
                            ))

                        if options:
                            from rich.prompt import Prompt
                            choices = []
                            for i, o in enumerate(options):
                                if isinstance(o, dict):
                                    choices.append(str(o.get("label", o.get("value", i))))
                                else:
                                    choices.append(str(o))
                            answer = Prompt.ask("[bold cyan]Choose option[/bold cyan]", choices=choices)
                        else:
                            answer = ctx.console.input("[bold cyan]Your response:[/bold cyan] ").strip()

                        new_history = list(history)
                        if prompt:
                            new_history.append({"role": "user", "content": prompt})
                        new_history.append({"role": "assistant", "content": full_content})
                        new_history.append({"role": "user", "content": f"Observation: {json.dumps({'answer': answer})}"})
                    
                        history = new_history
                        prompt = ""
                        agent_id = chunk.get("agent_id", agent_id)
                        _ask_user_triggered = True
                        break

            except Exception as e:
                log.exception("Streaming exception")
                safe_print(f"[bold red]Stream failed:[/bold red] {e}")
                if not _tokens_counted:
                    update_token_metrics(ctx, prompt, history, full_content, model)
                    ctx.save()
                    _tokens_counted = True
                _stream_errored = True

            finally:
                if client is not None:
                    await client.aclose()

        if _ask_user_triggered:
            continue
            
        ctx.console.print(Rule(title="[bold #ff00ea]COMM-LINK CLOSED[/bold #ff00ea]", style="bold #00f0ff"))

        if full_content and full_content.strip():
            ctx.console.print()
            ctx.console.print(Panel(str(full_content), title="[bold #ff00ea]SWARM OS RESPONSE[/bold #ff00ea]", border_style="bold #00f0ff", padding=(1, 2)))

        if not _tokens_counted:
            update_token_metrics(ctx, prompt, history, full_content, model)

        if _stream_errored:
            return history
            
        return history

def stream_prompt(ctx, agent_id, prompt, history):
    # BUG FIX: asyncio.run() raises RuntimeError if called from an already-running event loop.
    # This can happen when stream_prompt is invoked from Jupyter, tests, or certain frameworks.
    # Solution: if a loop is already running, offload to a fresh thread with its own loop.
    try:
        loop = asyncio.get_running_loop()
        # A loop is already running — run in a separate thread to avoid nesting
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, _stream_prompt_async(ctx, agent_id, prompt, history))
            return future.result()
    except RuntimeError:
        # No running loop — safe to use asyncio.run() directly
        return asyncio.run(_stream_prompt_async(ctx, agent_id, prompt, history))

def stream_prompt_with_retry(ctx, agent_id, prompt, history, max_retries=3):
    ctx.delegation_chain = [agent_id]
    delays = [2, 5, 10]
    for attempt in range(max_retries):
        result = stream_prompt(ctx, agent_id, prompt, history)
        if len(result) > len(history):
            if getattr(ctx, "speech_enabled", False):
                from organism_console.speech import speak_async, play_chime_async
                last_msg = result[-1].get("content", "") if result else ""
                if last_msg:
                    speak_async(last_msg)
                play_chime_async("success")
            return result
        if attempt < max_retries - 1:
            delay = delays[attempt]
            ctx.console.print(
                f"[bold yellow]  Retry {attempt+1}/{max_retries - 1}[/bold yellow] "
                f"[dim]in {delay}s...[/dim]"
            )
            # BUG FIX: Use a single time.sleep(delay) instead of a busy-loop of 0.1s sleeps
            time.sleep(delay)
                
    if getattr(ctx, "speech_enabled", False):
        from organism_console.speech import play_chime_async
        play_chime_async("error")
        
    ctx.console.print("[bold red]All retry attempts exhausted.[/bold red]")
    return history
