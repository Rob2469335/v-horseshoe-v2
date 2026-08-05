"""Screen control — the swarm's computer-use tier (read + propose, then act).

Mirrors the Anthropic Computer Use loop (screenshot → decide → act) natively on
Windows via win32 APIs (no pyautogui/mss dependencies).

SECURITY MODEL:
  - HUMAN-CONTROL MODE IS THE DEFAULT: input actions (click, type, scroll, key,
    mouse_move) are BLOCKED and return a "propose first" result until
    `swarm_os.lib.mcp.screen.SCREEN_AUTONOMOUS` is True (set via env
    SWARM_SCREEN_AUTONOMOUS=1 or `set_screen_autonomous(True)`).
  - Read-only actions (screenshot, cursor_position, foreground_window,
    list_windows) are always allowed — the agent can SEE and PROPOSE but not
    TOUCH without the flag.
  - Action cap: total input actions per session are capped (default 200) to
    stop runaway loops (Rosply-style); `reset_screen_action_count()` resets.
  - Loopback-only: this module never opens a network port.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict

log = logging.getLogger(__name__)

import ctypes

IS_WINDOWS = getattr(ctypes, "windll", None) is not None

if IS_WINDOWS:
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
else:
    # Non-Windows (CI runners, macOS, Linux): win32 bindings are unavailable.
    # The module must still import so tests/collection and tool registration
    # work; every handler returns a graceful "not supported" error instead.
    wintypes = None
    user32 = None
    kernel32 = None

# ---------------------------------------------------------------------------
# Mode & guards
# ---------------------------------------------------------------------------

SCREEN_AUTONOMOUS = os.getenv("SWARM_SCREEN_AUTONOMOUS", "").lower() in ("1", "true", "yes", "on")
_SCREEN_MAX_ACTIONS = int(os.getenv("SWARM_SCREEN_MAX_ACTIONS", "200"))
_screen_action_count = 0

# Input actions that change machine state; gated by autonomous mode.
_INPUT_ACTIONS = {"mouse_move", "left_click", "right_click", "double_click", "scroll", "type", "key"}

# Risk-tiered authorization (2026 computer-use best practice — systemshardening
# "Computer Use Sandboxing", JARVIS survey): even in AUTONOMOUS mode, high-risk
# input must NOT auto-execute. Risky patterns = typing secrets, opening URLs,
# system shortcuts, destructive keystrokes. They are routed to human approval
# unless SWARM_SCREEN_AUTO_APPROVE_RISKY=1 (operator explicitly opts in).
_RISKY_KEY_COMBOS = {
    ("ctrl", "alt", "del"), ("ctrl", "alt", "delete"),
    ("win", "l"), ("cmd", "l"), ("win", "r"), ("cmd", "r"),
    ("alt", "f4"), ("ctrl", "shift", "esc"),
    ("win", "e"), ("cmd", "e"), ("win", "x"), ("cmd", "x"),
    ("super", "l"), ("super", "r"),
}
_RISKY_TEXT_PATTERNS = (
    ("password", "typing a password-like value"),
    ("token", "typing a token value"),
    ("api_key", "typing an API key"),
    ("secret", "typing a secret"),
    ("apikey", "typing an API key"),
)
_SCREEN_AUTO_APPROVE_RISKY = os.getenv("SWARM_SCREEN_AUTO_APPROVE_RISKY", "").lower() in ("1", "true", "yes", "on")

# Append-only audit log of every input action + authorization decision.
_AUDIT_LOG_PATH = os.path.join(os.getcwd(), "logs", "screen_audit.jsonl")
# Time-based runaway guard: a single uninterrupted input session longer than
# this (no reset) is a likely loop — block further input until the count resets.
_RUNAWAY_WINDOW_S = int(os.getenv("SWARM_SCREEN_RUNAWAY_WINDOW_S", "180"))
_first_input_ts = 0.0

_SCREENSHOT_DIR = os.getenv("SWARM_SCREENSHOT_DIR", os.path.join(os.getcwd(), "logs", "screenshots"))


def _ok(result: Any) -> Dict[str, Any]:
    return {"ok": True, "result": result}


def _err(message: str) -> Dict[str, Any]:
    return {"ok": False, "error": str(message)}


def _audit(entry: Dict[str, Any]) -> None:
    """Append one authorization/execution record to the audit log (best-effort)."""
    try:
        import json
        os.makedirs(os.path.dirname(_AUDIT_LOG_PATH), exist_ok=True)
        entry["ts"] = time.time()
        with open(_AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception as exc:
        log.warning("Screen audit write failed: %s", exc)


def set_screen_autonomous(value: bool) -> Dict[str, Any]:
    global SCREEN_AUTONOMOUS
    SCREEN_AUTONOMOUS = bool(value)
    _audit({"decision": "autonomous_set", "value": SCREEN_AUTONOMOUS})
    return _ok({"screen_autonomous": SCREEN_AUTONOMOUS, "note": "All input actions enabled. THIS CONTROLS YOUR REAL MOUSE AND KEYBOARD."})


def reset_screen_action_count() -> Dict[str, Any]:
    global _screen_action_count, _first_input_ts
    _screen_action_count = 0
    _first_input_ts = 0.0
    _audit({"decision": "reset"})
    return _ok({"actions_reset": True, "cap": _SCREEN_MAX_ACTIONS})


def _risk_reason(action: str, kwargs: Dict[str, Any]) -> str | None:
    """Return a human-readable risk reason if the action is risky, else None.
    Applies on top of the autonomous gate — risky actions need explicit opt-in."""
    if action == "key":
        parts = sorted(p.lower() for p in str(kwargs.get("name", "")).split("+") if p.strip())
        for combo in _RISKY_KEY_COMBOS:
            if parts == sorted(combo):
                return f"system shortcut '{kwargs.get('name')}'"
    elif action == "type":
        text = str(kwargs.get("text", ""))
        lowered = text.lower()
        for needle, label in _RISKY_TEXT_PATTERNS:
            if needle in lowered:
                return label
    return None


def _spend_action(action: str, kwargs: Dict[str, Any] | None = None) -> Dict[str, Any] | None:
    """Check human-control gate + risk gate + caps. Returns an error result if
    blocked, else None."""
    global _screen_action_count, _first_input_ts
    kwargs = kwargs or {}
    if action in _INPUT_ACTIONS:
        risk = _risk_reason(action, kwargs)
        # Risk gate applies even in autonomous mode — unless operator opted in.
        if risk and not _SCREEN_AUTO_APPROVE_RISKY:
            _audit({"action": action, "decision": "approval_required", "reason": risk, "kwargs": kwargs})
            return _err(
                f"RISK GATE: '{action}' would {risk} — this requires human approval even in "
                "autonomous mode. Set SWARM_SCREEN_AUTO_APPROVE_RISKY=1 to allow, or describe "
                f"the action ({kwargs}) and wait for a human to run it."
            )
        if not SCREEN_AUTONOMOUS:
            return _err(
                "HUMAN-CONTROL MODE: the swarm may NOT move the mouse or send input yet. "
                "It can still screenshot and read the screen. To enable autonomous input, set "
                "SWARM_SCREEN_AUTONOMOUS=1 (or call set_screen_autonomous(true)). "
                f"Proposed action: '{action}' — describe what you would do and wait for approval."
            )
        now = time.time()
        if _first_input_ts and (now - _first_input_ts) > _RUNAWAY_WINDOW_S:
            _audit({"action": action, "decision": "blocked_runaway", "window_s": _RUNAWAY_WINDOW_S})
            return _err(
                f"RUNAWAY GUARD: screen input has been running continuously for >{_RUNAWAY_WINDOW_S}s "
                "without a reset. Call reset_screen_action_count() to continue. Sustained input is a loop risk."
            )
        if not _first_input_ts:
            _first_input_ts = now
        _screen_action_count += 1
        if _screen_action_count > _SCREEN_MAX_ACTIONS:
            return _err(
                f"Action cap reached ({_SCREEN_MAX_ACTIONS} input actions). "
                "Call reset_screen_action_count() or stop. Runaway loop guard triggered."
            )
    return None


# ---------------------------------------------------------------------------
# Screen capture
# ---------------------------------------------------------------------------

def _screen_size() -> tuple[int, int]:
    w = user32.GetSystemMetrics(0)
    h = user32.GetSystemMetrics(1)
    return int(w), int(h)


def screenshot(save: bool = True) -> Dict[str, Any]:
    """Capture the full screen. Returns the PNG path + dimensions + foreground window."""
    try:
        import win32con
        import win32gui
        import win32ui
        from PIL import Image

        w, h = _screen_size()
        w = max(1, w)
        h = max(1, h)
        try:
            hwnd_desktop = win32gui.GetDesktopWindow()
            hwnd_dc = win32gui.GetWindowDC(hwnd_desktop)
            mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
            save_dc = mfc_dc.CreateCompatibleDC()
            bitmap = win32ui.CreateBitmap()
            bitmap.CreateCompatibleBitmap(mfc_dc, w, h)
            save_dc.SelectObject(bitmap)
            save_dc.BitBlt((0, 0), (w, h), mfc_dc, (0, 0), win32con.SRCCOPY)

            bmpinfo = bitmap.GetInfo()
            bmpdata = bitmap.GetBitmapBits(True)
            img = Image.frombuffer("RGB", (bmpinfo["bmWidth"], bmpinfo["bmHeight"]), bmpdata, "raw", "BGRX", 0, 1)

            save_dc.DeleteDC()
            mfc_dc.DeleteDC()
            win32gui.DeleteObject(bitmap.GetHandle())
        except Exception:
            # Fallback for headless/background Windows sessions without an attached GDI desktop
            img = Image.new("RGB", (w, h), (0, 0, 0))

        fg = foreground_window()
        if save:
            os.makedirs(_SCREENSHOT_DIR, exist_ok=True)
            path = os.path.join(_SCREENSHOT_DIR, time.strftime("screen_%Y%m%d_%H%M%S.png"))
            img.save(path, "PNG")
            return _ok({
                "path": path,
                "width": w,
                "height": h,
                "mode": "RGBA",
                "foreground_window": fg.get("result", {}).get("title", "") if fg.get("ok") else "",
            })
        # No-save mode: describe pixels only (no binary in the result).
        px = img.convert("L")
        cols = []
        for cx in (w * i // 4 for i in range(1, 4)):
            cols.append(px.getpixel((cx, h // 2)))
        return _ok({
            "width": w,
            "height": h,
            "center_luma_columns": cols,
            "note": "Screenshot not saved; call screenshot(save=true) to get a PNG path.",
            "foreground_window": fg.get("result", {}).get("title", "") if fg.get("ok") else "",
        })
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Window introspection (read-only)
# ---------------------------------------------------------------------------

def _window_title(hwnd: int) -> str:
    try:
        import win32gui
        return win32gui.GetWindowText(hwnd)
    except Exception:
        return ""


def foreground_window() -> Dict[str, Any]:
    try:
        import win32gui
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return _ok({"hwnd": 0, "title": "", "rect": [0, 0, 0, 0]})
        try:
            title = win32gui.GetWindowText(hwnd)
        except Exception:
            title = ""
        try:
            rect = list(win32gui.GetWindowRect(hwnd))
        except Exception:
            rect = [0, 0, 0, 0]
        return _ok({"hwnd": hwnd, "title": title, "rect": rect})
    except Exception as exc:
        return _err(exc)


def list_windows(max_results: int = 30) -> Dict[str, Any]:
    try:
        import win32gui
        windows = []

        def _cb(hwnd, _extra):
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd)
            if title:
                try:
                    rect = list(win32gui.GetWindowRect(hwnd))
                except Exception:
                    rect = [0, 0, 0, 0]
                windows.append({"hwnd": hwnd, "title": title[:150], "rect": rect})
            return True

        try:
            win32gui.EnumWindows(_cb, None)
        except Exception:
            windows = [{"hwnd": 0, "title": "Background Desktop Session", "rect": [0, 0, 0, 0]}]

        windows = windows[: int(max_results)]
        return _ok({"count": len(windows), "windows": windows})
    except Exception as exc:
        return _err(exc)


def cursor_position() -> Dict[str, Any]:
    try:
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        return _ok({"x": int(pt.x), "y": int(pt.y)})
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Input actions (gated by human-control mode)
# ---------------------------------------------------------------------------

def mouse_move(x: int, y: int) -> Dict[str, Any]:
    blocked = _spend_action("mouse_move", {"x": x, "y": y})
    if blocked:
        return blocked
    try:
        user32.SetCursorPos(int(x), int(y))
        _audit({"action": "mouse_move", "decision": "executed", "x": int(x), "y": int(y)})
        return _ok({"action": "mouse_move", "x": int(x), "y": int(y)})
    except Exception as exc:
        return _err(exc)


def _click(button: int, x: int | None, y: int | None, double: bool = False) -> Dict[str, Any]:
    if x is not None and y is not None:
        user32.SetCursorPos(int(x), int(y))
        time.sleep(0.05)
    n = 2 if double else 1
    for _ in range(n):
        user32.mouse_event(button, 0, 0, 0, 0)
        user32.mouse_event(button | 0x0002, 0, 0, 0, 0)
        time.sleep(0.08)
    return _ok({"action": "click", "button": button, "x": x, "y": y, "double": double})


def left_click(x: int | None = None, y: int | None = None) -> Dict[str, Any]:
    blocked = _spend_action("left_click", {"x": x, "y": y})
    if blocked:
        return blocked
    try:
        r = _click(0x0002, x, y)
        _audit({"action": "left_click", "decision": "executed", "x": x, "y": y})
        return r
    except Exception as exc:
        return _err(exc)


def right_click(x: int | None = None, y: int | None = None) -> Dict[str, Any]:
    blocked = _spend_action("right_click", {"x": x, "y": y})
    if blocked:
        return blocked
    try:
        r = _click(0x0008, x, y)
        _audit({"action": "right_click", "decision": "executed", "x": x, "y": y})
        return r
    except Exception as exc:
        return _err(exc)


def double_click(x: int | None = None, y: int | None = None) -> Dict[str, Any]:
    blocked = _spend_action("double_click", {"x": x, "y": y})
    if blocked:
        return blocked
    try:
        r = _click(0x0002, x, y, double=True)
        _audit({"action": "double_click", "decision": "executed", "x": x, "y": y})
        return r
    except Exception as exc:
        return _err(exc)


def scroll(direction: str = "down", amount: int = 3, x: int | None = None, y: int | None = None) -> Dict[str, Any]:
    blocked = _spend_action("scroll", {"direction": direction, "amount": amount, "x": x, "y": y})
    if blocked:
        return blocked
    try:
        if x is not None and y is not None:
            user32.SetCursorPos(int(x), int(y))
            time.sleep(0.05)
        delta = int(amount) * (120 if str(direction).lower() == "up" else -120)
        user32.mouse_event(0x0800, 0, 0, delta, 0)
        _audit({"action": "scroll", "decision": "executed", "direction": direction, "amount": int(amount)})
        return _ok({"action": "scroll", "direction": direction, "amount": int(amount)})
    except Exception as exc:
        return _err(exc)


def _send_unicode(text: str) -> None:
    """Type text via SendInput with KEYEVENTF_UNICODE — handles any charset."""
    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

    class INPUTUNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("union", INPUTUNION)]

    extra = ctypes.c_ulong(0)
    for ch in str(text):
        for down in (True, False):
            ki = KEYBDINPUT(0, ord(ch), 0x0004 if down else (0x0004 | 0x0002), 0, ctypes.pointer(extra))
            inp = INPUT(1, INPUTUNION(ki=ki))
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
            time.sleep(0.01)


def type_text(text: str) -> Dict[str, Any]:
    blocked = _spend_action("type", {"text": text})
    if blocked:
        return blocked
    try:
        _send_unicode(text)
        _audit({"action": "type", "decision": "executed", "chars": len(str(text))})
        return _ok({"action": "type", "chars": len(str(text))})
    except Exception as exc:
        return _err(exc)


def key(name: str) -> Dict[str, Any]:
    """Press a named key or key combo, e.g. 'enter', 'ctrl+s', 'alt+tab', 'esc'."""
    blocked = _spend_action("key", {"name": name})
    if blocked:
        return blocked
    try:
        _VK = {
            "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
            "space": 0x20, "backspace": 0x08, "delete": 0x2E, "del": 0x2E,
            "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
            "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
            "ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10,
            "win": 0x5B, "cmd": 0x5B, "menu": 0x5D,
            "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
            "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
            "a": 0x41, "b": 0x42, "c": 0x43, "d": 0x44, "e": 0x45, "f": 0x46,
            "g": 0x47, "h": 0x48, "i": 0x49, "j": 0x4A, "k": 0x4B, "l": 0x4C,
            "m": 0x4D, "n": 0x4E, "o": 0x4F, "p": 0x50, "q": 0x51, "r": 0x52,
            "s": 0x53, "t": 0x54, "u": 0x55, "v": 0x56, "w": 0x57, "x": 0x58,
            "y": 0x59, "z": 0x5A,
            "0": 0x30, "1": 0x31, "2": 0x32, "3": 0x33, "4": 0x34, "5": 0x35,
            "6": 0x36, "7": 0x37, "8": 0x38, "9": 0x39,
        }
        parts = [p.strip().lower() for p in str(name).split("+") if p.strip()]
        codes = [_VK.get(p, ord(p[0]) if len(p) == 1 else None) for p in parts]
        if any(c is None for c in codes):
            return _err(f"Unknown key '{name}'. Use one of: enter, tab, esc, space, backspace, delete, arrows, ctrl+s, alt+tab, a-z, 0-9, f1-f12.")
        for c in codes:
            user32.keybd_event(c, 0, 0, 0)
        for c in reversed(codes):
            user32.keybd_event(c, 0, 0x0002, 0)
        _audit({"action": "key", "decision": "executed", "name": name})
        return _ok({"action": "key", "name": name})
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

_HANDLERS = {
    "screenshot": screenshot,
    "cursor_position": cursor_position,
    "foreground_window": foreground_window,
    "list_windows": list_windows,
    "mouse_move": mouse_move,
    "left_click": left_click,
    "right_click": right_click,
    "double_click": double_click,
    "scroll": scroll,
    "type": type_text,
    "key": key,
    "set_screen_autonomous": set_screen_autonomous,
    "reset_screen_action_count": reset_screen_action_count,
}


def screen_handler(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Route `action=<name>` to a screen-control tool. Human-control gate enforced."""
    if not IS_WINDOWS:
        return _err("Screen control is Windows-only; not supported on this platform")
    action = str(payload.get("action", "") or "").lower().strip()
    handler = _HANDLERS.get(action)
    if not handler:
        return _err(f"Unknown screen action '{action}'. Available: {', '.join(sorted(_HANDLERS))}")
    # Enforce the human-control gate BEFORE argument validation so blocked
    # input actions always report the mode guard, not a kwarg error.
    if action in _INPUT_ACTIONS and not SCREEN_AUTONOMOUS:
        return _err(
            "HUMAN-CONTROL MODE: the swarm may NOT move the mouse or send input yet. "
            "It can still screenshot and read the screen. To enable autonomous input, set "
            "SWARM_SCREEN_AUTONOMOUS=1 (or call set_screen_autonomous(true)). "
            f"Proposed action: '{action}' — describe what you would do and wait for approval."
        )
    # SECURITY: `set_screen_autonomous`/`reset_screen_action_count` are themselves
    # self-bypass primitives — an agent in human-control mode must NOT be able to
    # flip itself into autonomous mode (real mouse/keyboard takeover) or reset the
    # runaway-action cap. Both are only honored when the OPERATOR already enabled
    # autonomous mode via SWARM_SCREEN_AUTONOMOUS=1.
    if action in ("set_screen_autonomous", "reset_screen_action_count") and not SCREEN_AUTONOMOUS:
        return _err(
            "HUMAN-CONTROL MODE: you cannot enable autonomous input or reset the "
            "action cap yourself. An operator must set SWARM_SCREEN_AUTONOMOUS=1. "
            f"Proposed action: '{action}' — describe what you would do and wait for approval."
        )
    kwargs = {k: v for k, v in payload.items() if k not in ("action", "tool", "capability")}
    try:
        return handler(**kwargs)
    except TypeError as exc:
        return _err(f"Invalid arguments for '{action}': {exc}")
    except Exception as exc:
        return _err(exc)
