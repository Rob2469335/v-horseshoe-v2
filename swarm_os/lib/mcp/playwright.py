from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

async def playwright_handler(params: Dict[str, Any], trace_hook=None) -> Dict[str, Any]:
    """
    Handles web browsing operations using Playwright.
    """
    operation = params.get("operation", "navigate")
    url = params.get("url", "")
    import os
    from pathlib import Path
    root = Path(os.getenv("ZENITH_PROJECT_ROOT", Path(__file__).resolve().parent.parent.parent))
    
    try:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return {
                "ok": False, 
                "error": "Playwright is not installed. Please run 'pip install playwright' and 'playwright install'."
            }

        async with async_playwright() as p:
            # We use chromium by default as it's the most robust for automation
            try:
                browser = await p.chromium.launch(headless=True)
            except Exception as e:
                return {"ok": False, "error": f"Failed to launch browser: {e}"}
                
            context = await browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
            page = await context.new_page()

            if operation == "navigate":
                if not url:
                    return {"ok": False, "error": "URL is required for navigate operation"}
                
                logger.info(f"Playwright navigating to: {url}")
                await page.goto(url, wait_until="networkidle", timeout=30000)
                
                title = await page.title()
                content = await page.content()
                
                result = {
                    "ok": True,
                    "url": page.url,
                    "title": title,
                    "content_summary": content[:1000] + "..." if len(content) > 1000 else content,
                    "full_content_length": len(content)
                }
                
                if trace_hook:
                    trace_hook("playwright_navigate", {"url": url, "title": title})
                    
                await browser.close()
                return result

            elif operation == "screenshot":
                if not url:
                    return {"ok": False, "error": "URL is required for screenshot operation"}
                
                await page.goto(url, wait_until="networkidle", timeout=30000)
                # In a real tool we might save this to a file, but for MCP we return status
                # or a base64 string if requested. For now, we simulate the action.
                # Sandbox screenshot path
                screenshot_name = Path(params.get("path", "screenshot.png")).name
                screenshot_path = root / screenshot_name
                await page.screenshot(path=str(screenshot_path))
                
                await browser.close()
                return {
                    "ok": True,
                    "url": page.url,
                    "path": screenshot_path,
                    "message": "Screenshot captured successfully"
                }

            elif operation == "extract_text":
                if not url:
                    return {"ok": False, "error": "URL is required for extract_text operation"}
                
                await page.goto(url, wait_until="networkidle", timeout=30000)
                text = await page.evaluate("() => document.body.innerText")
                
                await browser.close()
                return {
                    "ok": True,
                    "url": page.url,
                    "text": text
                }

            await browser.close()
            return {"ok": False, "error": f"Unknown operation: {operation}"}

    except Exception as e:
        logger.exception("Playwright tool error")
        return {"ok": False, "error": str(e)}
