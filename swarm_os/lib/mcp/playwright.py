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
    """Extract the interactive accessibility tree as text (role, name, value)."""
    try:
        data = await page.accessibility.snapshot()
    except Exception:
        return []

    out = []

    def walk(node, depth=0):
        if not node:
            return
        role = node.get("role", "")
        name = node.get("name", "")
        val = node.get("value", "")
        interactive = role in ("button", "link", "textbox", "combobox", "checkbox", "radio", "menuitem", "tab", "searchbox")
        if interactive or (name and len(name) < 80):
            entry = {"role": role, "name": name}
            if val and role in ("textbox", "combobox", "searchbox"):
                entry["value"] = val
            out.append(entry)
        for child in node.get("children", []):
            walk(child, depth + 1)

    walk(data)
    return out


async def playwright_handler(params: Dict[str, Any], trace_hook=None) -> Dict[str, Any]:
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
            # Use role+name locator for a precise click.
            locator = page.get_by_role(target.get("role", "button"), name=target.get("name", ""))
            await locator.click(timeout=8000)
            await page.wait_for_load_state("domcontentloaded")
            return {"ok": True, "clicked": target.get("name"), "url": page.url}

        elif operation == "browser_type" or operation == "type":
            if not name and not selector:
                return {"ok": False, "error": "browser_type needs name or selector"}
            if selector:
                locator = page.locator(selector)
            else:
                locator = page.get_by_role("textbox", name=name) if name else None
                if locator is None:
                    locator = page.get_by_role("searchbox", name=name)
            await locator.fill(value or text, timeout=8000)
            return {"ok": True, "typed_into": name or selector}

        elif operation == "browser_state":
            tabs = [{"title": (await p.title()) if p else "", "url": p.url} for p in _context.pages]
            return {"ok": True, "tab_count": len(tabs), "tabs": tabs, "profile": str(_PROFILE_DIR)}

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
