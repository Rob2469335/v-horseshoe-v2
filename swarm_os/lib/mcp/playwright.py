"""Persistent, accessibility-tree-driven browser tool (2026 SOTA for a local
text model on Windows).

The 2026 verdict for a small local model (no vision) is: drive the browser by its
ACCESSIBILITY TREE AS TEXT (buttons/links/inputs and their labels), not by
screenshot+click pixels. A 4B text model cannot produce reliable click
coordinates; it CAN reliably say "click the button named Save". Screenshots are
demoted to verification/fallback.

Key upgrades over the old scrape-only handler:
- PERSISTENT headed Chromium with a dedicated user-data-dir, so logins persist
  across calls (the user's browser state, not a throwaway headless instance).
- `browser_a11y`: dump the interactive accessibility tree as text.
- `browser_click`/`browser_type`/`browser_select`: operate on a11y role+name.
- `browser_state`: report open tabs.
- SSRF guard on every navigation (unchanged, must survive).
- Gated input ops (click/type on the user's real browser) are the agent's job to
  route through the approval gate at the tool_executor level.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Dict

from swarm_os.lib.mcp.web_search import _ssrf_check

logger = logging.getLogger(__name__)

# Secrets redaction (playwright-mcp `--secrets` equivalent): any value in the
# env vars listed here is scrubbed from browser tool responses so credentials the
# agent types (or that pages echo back) never reach the model.
_REDACT_ENV_KEYS = ("PASSWORD", "SECRET", "TOKEN", "API_KEY", "APP_PASSWORD")
_REDACT_VALUES = None


def _redact_values() -> list:
    global _REDACT_VALUES
    if _REDACT_VALUES is None:
        vals = set()
        for k, v in os.environ.items():
            if any(s in k.upper() for s in _REDACT_ENV_KEYS) and v and len(v) >= 4:
                vals.add(v)
        _REDACT_VALUES = sorted(vals, key=len, reverse=True)
    return _REDACT_VALUES


def _redact(value: Any) -> Any:
    """Recursively scrub known secrets from a browser result dict/str."""
    secrets = _redact_values()
    if not secrets:
        return value
    if isinstance(value, str):
        out = value
        for s in secrets:
            if s in out:
                out = out.replace(s, "[REDACTED]")
        return out
    if isinstance(value, list):
        return [_redact(v) for v in value]
    if isinstance(value, dict):
        return {k: _redact(v) for k, v in value.items()}
    return value

_PROFILE_DIR = Path(os.getenv("ZENITH_BROWSER_PROFILE", "data/browser_profile"))

# A single persistent browser+context shared across calls (module-level lazy).
_browser = None
_context = None
_lock = asyncio.Lock()
_pages: dict[str, Any] = {}


def _get_project_root() -> Path:
    return Path(os.getenv("ZENITH_PROJECT_ROOT", Path(__file__).resolve().parent.parent.parent))


async def _ensure_browser():
    global _browser, _context
    if _context is not None:
        return
    async with _lock:
        if _context is not None:
            return
        from playwright.async_api import async_playwright
        _browser = await async_playwright().start()
        _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            context = await _browser.chromium.launch_persistent_context(
                user_data_dir=str(_PROFILE_DIR),
                headless=False,
                viewport={"width": 1280, "height": 720},
            )
        except Exception:
            # If launch_persistent_context is unavailable, fall back to a
            # standard browser + context (session won't persist, still usable).
            browser = await _browser.chromium.launch(headless=True)
            context = await browser.new_context(viewport={"width": 1280, "height": 720})
        _context = context


async def _a11y_snapshot(page) -> list[dict]:
    """Extract the interactive elements (buttons/links/inputs/selects) as TEXT
    with their labels — the DOM-equivalent of an accessibility tree.

    NOTE: `page.accessibility.snapshot()` does NOT exist in this Playwright
    build (the API was removed), so we read the interactive surface from the DOM
    directly: every button/link/textbox/checkbox/radio/select with its visible
    label/placeholder/value. This is what modern Playwright-MCP does, and it's
    the right interface for a text model ("click the button named Save")."""
    js = """
    () => {
      const out = [];
      const add = (role, el) => {
        const label =
          (el.getAttribute('aria-label') || '') ||
          (el.getAttribute('placeholder') || '') ||
          (el.getAttribute('value') || '') ||
          (el.textContent || '').trim().slice(0, 80);
        const name = (el.getAttribute('name') || '') || '';
        if (label || name) {
          out.push({ role, name: label || name, value: el.value || '' });
        }
      };
      document.querySelectorAll('button, [role=button]').forEach(el => add('button', el));
      document.querySelectorAll('a[href]').forEach(el => add('link', el));
      document.querySelectorAll('input[type=text], input[type=email], input[type=search], input:not([type]), textarea, [contenteditable=true]').forEach(el => add('textbox', el));
      document.querySelectorAll('select').forEach(el => add('combobox', el));
      document.querySelectorAll('input[type=checkbox]').forEach(el => add('checkbox', el));
      document.querySelectorAll('input[type=radio]').forEach(el => add('radio', el));
      return out;
    }
    """
    try:
        return await page.evaluate(js)
    except Exception:
        return []


async def _find_element(page, role: str, name: str):
    """Resolve a locator for an element by a11y name (placeholder/label/value)
    OR raw name attribute — the two are often different, and matching only one
    silently fails. Returns a Playwright locator or None.

    Strategy: try get_by_role with the name, then fall back to CSS selectors for
    the raw name attr / placeholder, so both "click the button named Save" and
    "type into the input named q" work on real pages."""
    if not name:
        return None
    # 1) role+name locator (the accessible-name match).
    try:
        loc = page.get_by_role(role, name=name)
        if await loc.count() > 0:
            return loc
    except Exception:
        pass
    # 2) CSS: [name=...] (raw attribute) then [placeholder=...] (visible label).
    for sel in (f'[name="{name}"]', f'[placeholder="{name}"]', f'[aria-label="{name}"]'):
        try:
            loc = page.locator(sel)
            if await loc.count() > 0:
                return loc
        except Exception:
            pass
    return None


async def playwright_handler(params: Dict[str, Any], trace_hook=None) -> Dict[str, Any]:
    """Public entry: runs the browser operation and redacts secrets from the
    result before returning it to the agent/console (playwright-mcp --secrets)."""
    result = await _playwright_impl(params, trace_hook)
    return _redact(result)


async def _playwright_impl(params: Dict[str, Any], trace_hook=None) -> Dict[str, Any]:
    operation = params.get("operation", "navigate")
    url = params.get("url", "")
    role = params.get("role")
    name = params.get("name")
    value = params.get("value", "")
    text = params.get("text", "")
    selector = params.get("selector")

    if operation in ("navigate", "screenshot", "extract_text") and url:
        blocked = _ssrf_check(url)
        if blocked:
            return {"ok": False, "error": f"SSRF blocked: {blocked}"}

    try:
        from playwright.async_api import TimeoutError as PWTimeout
    except ImportError:
        return {"ok": False, "error": "Playwright is not installed. Run 'pip install playwright'."}

    try:
        await _ensure_browser()
        if _context is None:
            return {"ok": False, "error": "browser failed to start"}

        # If no page exists yet, create one.
        pages = _context.pages
        page = pages[-1] if pages else await _context.new_page()

        if operation == "navigate":
            if not url:
                return {"ok": False, "error": "URL is required for navigate"}
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_load_state("domcontentloaded")
            title = await page.title()
            a11y = await _a11y_snapshot(page)
            return {
                "ok": True, "url": page.url, "title": title,
                "a11y": a11y[:60], "a11y_count": len(a11y),
                "hint": "Use browser_a11y for the full tree, browser_click/browser_type to act.",
            }

        elif operation == "browser_a11y" or operation == "a11y":
            a11y = await _a11y_snapshot(page)
            return {"ok": True, "url": page.url, "count": len(a11y), "a11y": a11y[:200]}

        elif operation == "browser_click" or operation == "click":
            if not name and not role:
                return {"ok": False, "error": "browser_click needs name (and optional role)"}
            a11y = await _a11y_snapshot(page)
            target = None
            for node in a11y:
                if name and name.lower() in str(node.get("name", "")).lower():
                    target = node
                    break
            if target is None:
                return {"ok": False, "error": f"no a11y node named '{name}' found; try browser_a11y first",
                        "available": [n.get("name") for n in a11y[:20]]}
            locator = await _find_element(page, target.get("role", "button"), name)
            if locator is None:
                return {"ok": False, "error": f"could not resolve click target '{name}'",
                        "available": [n.get("name") for n in a11y[:20]]}
            await locator.click(timeout=8000)
            await page.wait_for_load_state("domcontentloaded")
            return {"ok": True, "clicked": target.get("name"), "url": page.url}

        elif operation == "browser_type" or operation == "type":
            if not name and not selector:
                return {"ok": False, "error": "browser_type needs name or selector"}
            locator = None
            if selector:
                locator = page.locator(selector)
            elif name:
                locator = await _find_element(page, "textbox", name)
                if locator is None:
                    locator = await _find_element(page, "combobox", name)
                if locator is None:
                    locator = await _find_element(page, "searchbox", name)
            if locator is None:
                a11y = await _a11y_snapshot(page)
                return {"ok": False, "error": f"no input named '{name}' found; try browser_a11y first",
                        "available": [n.get("name") for n in a11y[:20]]}
            await locator.fill(value or text, timeout=8000)
            return {"ok": True, "typed_into": name or selector}

        elif operation == "browser_fill_form" or operation == "fill_form":
            """Multi-field form fill. `fields` is a list of {name, value} — each
            field is resolved by its a11y name (label/placeholder/aria) or raw
            name attr, filled, then the fill is VERIFIED by reading the value
            back. This is the 2026 SOTA form-fill primitive (playwright-mcp
            browser_fill_form): never guess selectors, verify before submit."""
            fields = params.get("fields") or []
            if not isinstance(fields, list) or not fields:
                return {"ok": False, "error": "browser_fill_form needs fields=[{name, value}]"}
            filled, failed = [], []
            for f in fields:
                fname = f.get("name", "")
                fvalue = f.get("value", "")
                locator = None
                for role in ("textbox", "combobox", "searchbox", "textarea"):
                    locator = await _find_element(page, role, fname)
                    if locator is not None:
                        break
                if locator is None:
                    locator = await _find_element(page, "textbox", fname)
                if locator is None:
                    failed.append({"name": fname, "error": "not found"})
                    continue
                try:
                    await locator.fill(fvalue, timeout=6000)
                    # Verify the value actually landed.
                    actual = await locator.input_value() if await locator.count() else ""
                    if str(actual) == str(fvalue):
                        filled.append(fname)
                    else:
                        failed.append({"name": fname, "error": f"value mismatch: got {actual!r}"})
                except Exception as exc:
                    failed.append({"name": fname, "error": str(exc)})
            # 2026 form-fill hardening: after filling, run the form's OWN
            # constraint-validation API to detect required fields the planner
            # missed — strictly more reliable than asking the model to eyeball it.
            try:
                validity = await page.evaluate("""() => {
                  const out = [];
                  document.querySelectorAll('form').forEach((form, fi) => {
                    if (typeof form.checkValidity === 'function' && !form.checkValidity()) {
                      form.querySelectorAll('input,select,textarea').forEach((el) => {
                        if (el.required && !el.value) {
                          out.push({ form: fi, field: el.getAttribute('name') || el.getAttribute('id') || el.placeholder || '', reason: 'required and empty' });
                        } else if (el.validity && !el.validity.valid) {
                          out.push({ form: fi, field: el.getAttribute('name') || el.placeholder || '', reason: el.validationMessage || 'invalid' });
                        }
                      });
                    }
                  });
                  return out;
                }""")
                if validity and not failed:
                    return {"ok": False, "filled": filled, "failed": failed,
                            "incomplete": validity, "url": page.url,
                            "error": "form has required/invalid fields after fill"}
            except Exception:
                pass  # validity check is best-effort
            return {"ok": not failed, "filled": filled, "failed": failed, "url": page.url}

        elif operation == "browser_verify" or operation == "verify":
            """Read a field's current value back to confirm a prior fill landed —
            the deterministic 'did the value stick' check."""
            if not name and not selector:
                return {"ok": False, "error": "browser_verify needs name or selector"}
            locator = None
            if selector:
                locator = page.locator(selector)
            else:
                for role in ("textbox", "combobox", "searchbox"):
                    locator = await _find_element(page, role, name)
                    if locator is not None:
                        break
            if locator is None:
                return {"ok": False, "error": f"field '{name}' not found"}
            try:
                actual = await locator.input_value()
                return {"ok": True, "name": name, "value": actual}
            except Exception as exc:
                return {"ok": False, "error": str(exc)}

        elif operation == "browser_find" or operation == "find":
            """Search the a11y tree for elements matching text (cheaper than a
            full dump) — playwright-mcp's browser_find."""
            a11y = await _a11y_snapshot(page)
            q = str(params.get("query", "") or name or "").lower()
            hits = [n for n in a11y if q and q in str(n.get("name", "")).lower()]
            return {"ok": True, "query": q, "count": len(hits), "matches": hits[:30]}

        elif operation == "browser_press_key" or operation == "press":
            key = params.get("key") or params.get("value") or "Enter"
            await page.keyboard.press(key)
            await page.wait_for_load_state("domcontentloaded")
            return {"ok": True, "pressed": key, "url": page.url}

        elif operation == "browser_wait" or operation == "wait":
            import asyncio as _asyncio
            ms = int(params.get("ms", 1500))
            await _asyncio.sleep(ms / 1000.0)
            a11y = await _a11y_snapshot(page)
            return {"ok": True, "waited_ms": ms, "a11y": a11y[:40]}

        elif operation == "browser_state":
            tabs = [{"title": (await p.title()) if p else "", "url": p.url} for p in _context.pages]
            return {"ok": True, "tab_count": len(tabs), "tabs": tabs, "profile": str(_PROFILE_DIR)}

        elif operation == "browser_describe" or operation == "describe":
            """Vision fallback: screenshot the page and ask the local vision model
            (Qwen3-VL-2B on :8083) to describe it as text. Used when the a11y
            tree is empty (canvas/custom-rendered apps) — the 2026 OpenClaw
            pattern (image-model-describes -> text for a text-only agent)."""
            shot_path = _get_project_root() / "browser_describe.png"
            await page.screenshot(path=str(shot_path))
            try:
                import base64
                b64 = base64.b64encode(shot_path.read_bytes()).decode()
                # llama.cpp vision: OpenAI-compatible multimodal message with an
                # image_url data URI. Requires the vision server (:8083) to be up;
                # degrades gracefully (ok:False) if not.
                from swarm_os.infra.llama_client import LlamaClient
                vision = LlamaClient(base_url="http://127.0.0.1:8083")
                text = await vision.generate(
                    model="qwen3-vl",
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                            {"type": "text", "text": "Describe what is on this webpage, focusing on interactive elements (buttons, links, inputs) and their visible labels."},
                        ],
                    }],
                )
                return {"ok": True, "described": True, "description": str(text)[:2000]}
            except Exception as exc:
                logger.warning("browser_describe vision unavailable: %s", exc)
                return {"ok": False, "error": f"vision describe unavailable: {exc}"}

        elif operation == "screenshot":
            if url:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            name_ = Path(params.get("path", "browser.png")).name
            shot_path = _get_project_root() / name_
            await page.screenshot(path=str(shot_path))
            return {"ok": True, "url": page.url, "path": str(shot_path)}

        elif operation == "extract_text":
            if url:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            try:
                import markdownify
                html = await page.content()
                text_ = markdownify.markdownify(html, heading_style="ATX").strip()
            except Exception:
                text_ = (await page.inner_text("body"))[:8000]
            return {"ok": True, "url": page.url, "text": text_[:8000]}

        return {"ok": False, "error": f"Unknown operation: {operation}"}

    except PWTimeout:
        return {"ok": False, "error": "browser operation timed out"}
    except Exception as exc:
        logger.exception("Playwright tool error")
        return {"ok": False, "error": str(exc)}
