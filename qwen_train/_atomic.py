"""Atomic durable writes for the qwen_train runners.

Thin re-export of :mod:`swarm_os.lib.atomic_io` so a script launched as
``python qwen_train/x.py`` (script dir on sys.path) can import it without the
repo root already being importable. Use for result JSONL/JSON that is rewritten
wholesale — a crash mid-write must never truncate it.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from swarm_os.lib.atomic_io import (  # noqa: E402,F401
    atomic_write_json,
    atomic_write_jsonl,
    atomic_write_text,
)
