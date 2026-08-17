"""Desktop attention notifications (opencode's "attention" layer).

On Windows, a native balloon toast is fired through PowerShell's WinForms
NotifyIcon — no third-party module required. Everything runs in a daemon
thread and swallows failures so a headless/CI session never blocks or crashes.
"""

from __future__ import annotations

import subprocess
import sys
import threading

_ACTIVE = True


def set_enabled(enabled: bool) -> None:
    global _ACTIVE
    _ACTIVE = bool(enabled)


def enabled() -> bool:
    return _ACTIVE


def _toast_script(title: str, body: str) -> str:
    # Escape single quotes for the PowerShell single-quoted string literals.
    esc = lambda s: s.replace("'", "''")
    return (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "Add-Type -AssemblyName System.Drawing;"
        "$n = New-Object System.Windows.Forms.NotifyIcon;"
        "$n.Icon = [System.Drawing.SystemIcons]::Information;"
        "$n.BalloonTipIcon = 'Info';"
        f"$n.BalloonTipTitle = '{esc(title)}';"
        f"$n.BalloonTipText = '{esc(body)}';"
        "$n.Visible = $true;"
        "$n.ShowBalloonTip(5000);"
        "Start-Sleep -Seconds 6;"
        "$n.Dispose();"
    )


def notify(title: str, body: str = "") -> None:
    """Fire a desktop toast asynchronously. No-op off-Windows / when disabled."""
    global _ACTIVE
    if not _ACTIVE or sys.platform != "win32":
        return
    if not (title or body):
        return

    def _fire() -> None:
        try:
            subprocess.Popen(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    _toast_script(title, body),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            pass

    threading.Thread(target=_fire, daemon=True).start()
