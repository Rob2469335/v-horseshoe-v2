"""DangerRoom.scan_sandbox must scan repo files NON-strict.

Regression (2026-09-11): scan_sandbox called SecurityGate.scan_file(..., strict=True),
which bans pathlib/network modules in LLM-generated snippets. But the sandbox
copy contains the PROJECT's own files — agent_service_v2.py legitimately uses
`from pathlib import Path`. strict=True flagged it, so every mutation of a
pathlib-using repo file was rejected ("Evolution halted").

Fix: strict=False for repo-file scanning (the gate's documented mode for
scan_file). Strict stays the LLM-snippet mode. Test 2 proves strict=False still
blocks genuinely banned imports (no overcorrection).
"""

from __future__ import annotations

import pytest

from swarm_os.services.danger_room import DangerRoom
from swarm_os.services.security_gate import SecurityGateViolation


def _room(tmp_path):
    dr = DangerRoom(tmp_path)
    dr.sandbox_dir = tmp_path / "sbox"
    dr.sandbox_dir.mkdir()
    dr.is_active = True
    return dr


@pytest.mark.asyncio
async def test_scan_sandbox_allows_pathlib_in_repo_files(tmp_path):
    dr = _room(tmp_path)
    (dr.sandbox_dir / "agent_service_v2.py").write_text(
        "from pathlib import Path\nclass AgentServiceV2:\n    pass\n",
        encoding="utf-8",
    )
    # must NOT raise SecurityGateViolation
    await dr.scan_sandbox(specific_files=["agent_service_v2.py"])


@pytest.mark.asyncio
async def test_scan_sandbox_still_blocks_real_banned_import(tmp_path):
    dr = _room(tmp_path)
    # subprocess is wholesale-banned in BOTH strict and non-strict mode
    (dr.sandbox_dir / "evil.py").write_text("import subprocess\n", encoding="utf-8")
    with pytest.raises(SecurityGateViolation):
        await dr.scan_sandbox(specific_files=["evil.py"])