import asyncio
import pytest
from swarm_os.api.admin import workspace_changes


class _RunResult:
    def __init__(self, returncode, stdout, stderr=""):
        self.returncode = returncode
        self.stdout = stdout.encode("utf-8")
        self.stderr = stderr.encode("utf-8")
    
    async def communicate(self):
        return self.stdout, self.stderr


@pytest.mark.asyncio
async def test_workspace_changes_parses_git_output(monkeypatch):
    fake_stat = " a.py | 3 ++-\n b.py | 5 +++--\n 2 files changed, 5 insertions(+), 3 deletions(-)\n"
    fake_diff = (
        "diff --git a/a.py b/a.py\n"
        "index abc..def 100644\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,2 +1,3 @@\n"
        "-x = 1\n"
        "+x = 2\n"
        "+y = 3\n"
    )

    async def fake_run(*args, **kwargs):
        cmd = " ".join(str(a) for a in args) if args else kwargs.get("program", "")
        if isinstance(cmd, list): cmd = " ".join(cmd)
        if "--stat" in cmd:
            return _RunResult(0, fake_stat)
        return _RunResult(0, fake_diff)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_run)
    result = await workspace_changes()
    assert result["is_git"] is True
    assert result["truncated"] is False
    assert {"path": "a.py", "lines": 3} in result["stat"]
    assert {"path": "b.py", "lines": 5} in result["stat"]
    assert "+x = 2" in result["diff"]
    assert "@@ -1,2 +1,3 @@" in result["diff"]


@pytest.mark.asyncio
async def test_workspace_changes_respects_cap(monkeypatch):
    async def fake_run(*args, **kwargs):
        cmd = " ".join(str(a) for a in args) if args else kwargs.get("program", "")
        if isinstance(cmd, list): cmd = " ".join(cmd)
        body = "-" * 2000
        return _RunResult(0, body if "--stat" in cmd else body)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_run)
    result = await workspace_changes(max_diff_chars=1000)
    assert result["is_git"] is True
    assert result["truncated"] is True
    assert len(result["diff"]) <= 1000


@pytest.mark.asyncio
async def test_workspace_changes_not_git(monkeypatch):
    async def fake_run(*args, **kwargs):
        return _RunResult(128, "fatal: not a git repository (or any of the parent directories)")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_run)
    result = await workspace_changes()
    assert result["is_git"] is False
    assert result["diff"] == ""
    assert result["stat"] == []
