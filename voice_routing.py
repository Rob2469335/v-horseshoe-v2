"""Voice routing logic for ambient_listener.py dual-mode pipeline.

Extracted into its own module for testability independent of heavy
dependencies (torch, speechbrain, openwakeword, etc.).
"""

import os
import re
import time
import requests

if os.name == 'nt':
    import win32com
    # Redirect win32com's generated-code cache away from the shared C:\Windows\Temp
    # (which had a permissions issue) to a location fully owned by this user.
    try:
        win32com.__gen_path__ = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'Temp', 'gen_py')
        os.makedirs(win32com.__gen_path__, exist_ok=True)
    except OSError as e:
        print(f"Warning: Failed to set win32com cache path: {e}")
    import win32com.client


DICTATION_KEYWORDS = ["dictate", "type", "type this"]
LLM_API_URL = os.environ.get("LLM_API_URL", "http://127.0.0.1:4000/v1/chat/completions")
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen3.5-4b")
DICTATION_COUNTDOWN_SECONDS = 1
DICTATION_ESCAPE_CHARS_RE = re.compile(r'([{}\(\)\+\^%~])')


def determine_mode(transcript):
    """Check start of transcript for dictation keywords.

    Longer keywords are checked first to avoid partial matching.
    Leading whitespace and punctuation after the keyword are stripped.

    Returns (mode, cleaned_text) where mode is "dictation" or "agent_command".
    """
    stripped = transcript.strip()
    lowered = stripped.lower()
    for kw in sorted(DICTATION_KEYWORDS, key=len, reverse=True):
        if lowered.startswith(kw):
            cleaned = stripped[len(kw):].strip().lstrip(":, ")
            return "dictation", cleaned
    return "agent_command", transcript


def polish_transcript(raw_text):
    """Polish transcript via local LLM API (LiteLLM / OpenAI-compatible endpoint)."""
    prompt = (
        "Rewrite the following transcript into clean, technically accurate text. "
        "Output ONLY the corrected text with no preamble, no explanation, and no list of changes: "
        f"{raw_text}"
    )
    try:
        resp = requests.post(
            LLM_API_URL,
            json={
                "model": LLM_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if "choices" in data and len(data["choices"]) > 0:
            return data["choices"][0]["message"]["content"].strip()
        return raw_text
    except requests.exceptions.RequestException as e:
        print(f"  LLM polish failed (falling back to raw transcript): {e}")
        return raw_text


def type_text_via_sendkeys(text):
    """Type text into the focused window using WScript.Shell.SendKeys (pywin32)."""
    shell = win32com.client.Dispatch("WScript.Shell")
    escaped = DICTATION_ESCAPE_CHARS_RE.sub(r'{\1}', text)
    shell.SendKeys(escaped)

