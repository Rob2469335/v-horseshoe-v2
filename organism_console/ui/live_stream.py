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

_AGENT_PERF: dict = {}
_AGENT_PERF_MAX = 256

def update_token_metrics(ctx, prompt, history, output_content, model):
    input_tokens = estimate_tokens(prompt + json.dumps(history))
    output_tokens = estimate_tokens(output_content)
    ctx.total_input_tokens += input_tokens
    ctx.total_output_tokens += output_tokens
    model_name = (model or "unknown").lower()
    is_cloud = "cloud" in model_name or "groq" in model_name or "openrouter" in model_name
    if is_cloud or getattr(ctx, "last_provider", "llama.cpp") not in ("llama.cpp", "local"):
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

def status_footer(ctx, agent, model, phase, ram_pct, tps: float = 0.0):
    stats = get_system_stats()
    phase_colors = {
        "thinking": "white", "planning": "yellow", "sensing": "cyan",
        "repair": "red", "swarm": "magenta", "resume": "blue",
        "ocular": "bright_cyan", "executing": "bright_green",
    }
    pc = phase_colors.get(phase, "white")
    tps_str = f" tps:{tps:.1f}" if tps > 0 else ""
    return (
        f"[bright_black]φ[/bright_black] "
        f"[{pc}]{agent[:8]}[/{pc}]"
        f"[bright_black]@{model[:12]}[/bright_black]"
        f"[bold {stats['ram_color']}]{ram_pct:.0f}%[/bold {stats['ram_color']}]"
        f"[bright_green]{tps_str}[/bright_green]"
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

        ctx.console.print(Rule(title="", style="dim"))
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
                    if not err_msg and chunk_type == "error":
                        err_msg = chunk.get("content") or "Unknown error"
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
                                {"model": model, "role": chunk.get("requested_role", "unknown"),
                                 "attempt": chunk.get("attempt", 1), "temperature": chunk.get("temperature", 0.7)},
                                "green"
                            )
                            safe_print(panel)
                        else:
                            safe_print(render_step_micro_ui("model_selected", f"selected {model}"))
                        continue

                    if chunk_type == "model_escalation":
                        from_model = chunk.get("from_model")
                        reason = chunk.get("reason")
                        safe_print(f"[bold yellow]  Fallback:[/bold yellow] [dim]{from_model}[/dim] timed out "
                                   f"[bold yellow]→ Escalating to cloud[/bold yellow] [dim]({reason})[/dim]")
                        continue

                    if chunk_type == "agent_handoff":
                        from_a = chunk.get("from", agent_id)
                        to_a = chunk.get("to", "executor")
                        task = str(chunk.get("task", ""))[:80]
                        ctx.delegation_chain.append(to_a)
                        ctx.save()
                        handoffs_list.append({"from": from_a, "to": to_a, "task": task})
                        safe_print(f"  [bold magenta]→[/bold magenta] [cyan]{from_a}[/cyan] [dim]handing off to[/dim] [bold]{to_a}[/bold] [dim]{task}[/dim]")
                        continue

                    if chunk_type in ("tool_call", "tool_start"):
                        tool_name = chunk.get("tool") or chunk.get("name")
                        args_dict = chunk.get("arguments", {})
                        if args_dict:
                            safe_print(f"  [bold cyan]⚡[/bold cyan] [white]{tool_name}[/white]({json.dumps(args_dict, default=str)[:120]})")
                        else:
                            safe_print(f"  [bold cyan]⚡[/bold cyan] [white]{tool_name}[/white]")
                        continue

                    if chunk_type == "critic_update":
                        continue

                    if chunk_type == "tool_result":
                        tool = chunk.get("tool")
                        result = chunk.get("result", {})
                        ok = result.get("ok", False) if isinstance(result, dict) else True
                        icon = "[green]✓[/green]" if ok else "[red]✗[/red]"
                        result_str = str(result.get("result", result))[:100] if isinstance(result, dict) else str(result)[:100]
                        safe_print(f"  {icon} [bold]{tool}[/bold] [dim]{escape(result_str)}[/dim]")
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

                        footer = status_footer(ctx, agent_id, model, phase, stats["ram_pct"], tps)
                        layout.add_row(Text.from_markup(footer))

                        if display:
                            layout.add_row(Panel(Markdown(display), border_style="dim", padding=(0, 1)))

                        live.update(layout)
                        continue

                    if chunk_type == "final":
                        live.stop()
                        final_content = chunk.get("content", "")
                        ctx.last_provider = chunk.get("provider", "llama.cpp")

                        if isinstance(final_content, dict):
                            ctx.console.print(Panel(json.dumps(final_content, indent=2), title="[bold]Response[/bold]", border_style="bold #00f0ff"))
                        elif final_content:
                            ctx.console.print(Panel(Markdown(str(final_content)), title="", border_style="green", padding=(1, 2)))

                        new_history = list(history)
                        if prompt:
                            new_history.append({"role": "user", "content": prompt})
                        new_history.append({"role": "assistant", "content": final_content or full_content})
                        ctx.history = new_history
                        ctx.history_pointer = len(ctx.history) - 1

                        elapsed_total = time.time() - start_time
                        update_token_metrics(ctx, prompt, history, final_content or full_content, model)

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
                            ctx.console.print(Panel(Markdown(question), title="🛡️ [bold yellow]Approval Required[/bold yellow]", border_style="yellow", padding=(1, 2)))
                        else:
                            ctx.console.print(Panel(Markdown(question), title="❓ [bold cyan]Question[/bold cyan]", border_style="cyan", padding=(0, 1)))

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

        if full_content and full_content.strip():
            ctx.console.print()
            ctx.console.print(Panel(Markdown(str(full_content)), title="", border_style="green", padding=(1, 2)))

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
