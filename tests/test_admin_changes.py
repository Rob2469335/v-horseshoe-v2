import subprocess

from swarm_os.api.admin import workspace_changes


class _RunResult:
    def __init__(self, returncode, stdout):
        self.returncode = returncode
        self.stdout = stdout


def test_workspace_changes_parses_git_output(monkeypatch):
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

    def fake_run(cmd, *args, **kwargs):
        if "--stat" in cmd:
            return _RunResult(0, fake_stat)
        return _RunResult(0, fake_diff)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = workspace_changes()
    assert result["is_git"] is True
    assert result["truncated"] is False
    assert {"path": "a.py", "lines": 3} in result["stat"]
    assert {"path": "b.py", "lines": 5} in result["stat"]
    assert "+x = 2" in result["diff"]
    assert "@@ -1,2 +1,3 @@" in result["diff"]


def test_workspace_changes_respects_cap(monkeypatch):
    def fake_run(cmd, *args, **kwargs):
        body = "-" * 2000
        return _RunResult(0, body if "--stat" in cmd else body)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = workspace_changes(max_diff_chars=1000)
    assert result["is_git"] is True
    assert result["truncated"] is True
    assert len(result["diff"]) <= 1000


def test_workspace_changes_not_git(monkeypatch):
    def fake_run(cmd, *args, **kwargs):
        return _RunResult(128, "fatal: not a git repository (or any of the parent directories)")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = workspace_changes()
    assert result["is_git"] is False
    assert result["diff"] == ""
    assert result["stat"] == []
