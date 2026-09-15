"""Contamination filter for the FIX:-commit harvester (Item 1).

Proves the authoritative hash-denylist: eligible commits pass; denylisted commits are
rejected BEFORE becoming candidates (even when they would otherwise be valid); the rejection
reason is logged; and the ledger is append-only.
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "qwen_train"))

import mine_fix_commits as mfc  # noqa: E402


@pytest.fixture(autouse=True)
def global_subprocess_mock():
    yield


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )


def _init(tmp):
    _git(tmp, "init", "-q")
    _git(tmp, "config", "user.email", "t@t")
    _git(tmp, "config", "user.name", "t")
    _git(tmp, "config", "commit.gpgsign", "false")


def _repo_with_fix(tmp):
    """A repo whose HEAD is a FIX: commit touching a non-test .py (a harvestable candidate).

    Needs a PARENT commit first: git diff-tree on a root commit yields no files.
    """
    _init(tmp)
    (tmp / "base.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp, "add", "-A")
    _git(tmp, "commit", "-q", "-m", "initial")
    (tmp / "mod.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    _git(tmp, "add", "-A")
    _git(tmp, "commit", "-q", "-m", "FIX: add function")
    return _git(tmp, "rev-parse", "HEAD").stdout.strip()


def test_1_eligible_commit_passes(tmp_path):
    sha = _repo_with_fix(tmp_path)
    cs = mfc.fix_commits(repo=tmp_path, min_age_hours=0.0, denylist={})
    assert sha in [c["sha"] for c in cs]


def test_2_denylisted_commit_rejected(tmp_path):
    sha = _repo_with_fix(tmp_path)
    cs = mfc.fix_commits(
        repo=tmp_path,
        min_age_hours=0.0,
        denylist={sha.lower(): "excluded_contaminated_commit ctx"},
    )
    assert sha not in [c["sha"] for c in cs]


def test_4_reason_is_recorded(tmp_path, capsys):
    sha = _repo_with_fix(tmp_path)
    mfc.fix_commits(
        repo=tmp_path, min_age_hours=0.0, denylist={sha.lower(): "session FIX: x"}
    )
    out = capsys.readouterr().out
    assert "excluded_contaminated_commit" in out
    assert sha[:12] in out


def test_3_and_5_no_leakage_even_when_bug_would_be_valid(tmp_path, monkeypatch):
    """A denylisted commit that WOULD otherwise pass fail->pass validation is still rejected."""
    sha = _repo_with_fix(tmp_path)
    # Simulate a real flip WITHOUT running pytest: parent run fails, fix run is clean.
    calls = {"n": 0}

    def fake_run_failed(root, tests, timeout, python_exe):
        calls["n"] += 1
        return {"t::x"} if calls["n"] % 2 == 1 else set()

    monkeypatch.setattr(mfc, "run_failed", fake_run_failed)
    monkeypatch.setattr(mfc, "related_tests", lambda *a, **k: ["test_mod.py"])

    # with NO denylist this commit produces a usable task (proves it would otherwise pass)...
    usable = mfc.mine(n=5, repo=tmp_path, min_age_hours=0.0, denylist={})
    assert any(t["sha"] == sha for t in usable), usable

    # ...but denylisted, it cannot appear in the usable output.
    blocked = mfc.mine(
        n=5,
        repo=tmp_path,
        min_age_hours=0.0,
        denylist={sha.lower(): "excluded_contaminated_commit"},
    )
    assert blocked == []
    assert sha not in [t["sha"] for t in blocked]


def test_6_ledger_is_append_only(tmp_path):
    dl = tmp_path / "contaminated_commits.txt"
    dl.write_text("aaaa1111  excluded_contaminated_commit  first\n", encoding="utf-8")
    first = mfc.load_denylist(dl)
    assert "aaaa1111" in first

    with open(dl, "a", encoding="utf-8") as fh:
        fh.write("bbbb2222  excluded_contaminated_commit  second\n")
    after = mfc.load_denylist(dl)

    assert "aaaa1111" in after, "existing entry must survive an append"
    assert "bbbb2222" in after
    assert after["aaaa1111"] == "first"


def test_real_denylist_is_loadable_and_nonempty():
    d = mfc.load_denylist()
    assert len(d) >= 6, d
    # every entry carries the canonical reason token or context
    assert all(isinstance(v, str) and v for v in d.values())
