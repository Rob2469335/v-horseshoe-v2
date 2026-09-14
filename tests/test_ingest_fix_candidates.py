"""Tests for the candidate ingestion + soundness gate (qwen_train/ingest_fix_candidates.py).

Pins: parse of the Opus markdown format, and the SOUNDNESS rule — BROKEN must fail its
check AND FIXED must pass. An unsound candidate (broken passes, or fix doesn't fix) must
be rejected. Runs REAL `python check.py` subprocesses (override the conftest Popen mock).
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "qwen_train"))

import ingest_fix_candidates as ic  # noqa: E402


@pytest.fixture(autouse=True)
def global_subprocess_mock():
    yield


SOUND_MD = """### sample_add
family: EXPRESSION_VALUE
difficulty: 3
why_hard: plausible wrong operator

```python
def f(x):
    return x - 1
```
```python
def f(x):
    return x + 1
```
```python
from module import f
assert f(1) == 2
assert f(5) == 6
print('OK')
```
"""

UNSOUND_MD = """### not_a_bug
family: NOOP
difficulty: 2
why_hard: none

```python
def f(x):
    return x + 1
```
```python
def f(x):
    return x + 1
```
```python
from module import f
assert f(1) == 2
print('OK')
```
"""


def test_parse_markdown():
    cands = ic.parse_markdown(SOUND_MD)
    assert len(cands) == 1
    c = cands[0]
    assert c["kind"] == "sample_add"
    assert c["family"] == "EXPRESSION_VALUE"
    assert "x - 1" in c["broken"]
    assert "x + 1" in c["fixed"]
    assert "assert f(1) == 2" in c["check"]


def test_sound_candidate_passes_gate():
    c = ic.parse_markdown(SOUND_MD)[0]
    v = ic.check_soundness(c)
    assert v["sound"] is True, v["reasons"]
    assert v["rc_broken"] != 0 and v["rc_fixed"] == 0


def test_unsound_broken_passes_is_rejected():
    c = ic.parse_markdown(UNSOUND_MD)[0]
    v = ic.check_soundness(c)
    assert v["sound"] is False
    assert any("BROKEN module PASSED" in r for r in v["reasons"])


def test_ingest_splits_sound_and_unsound(tmp_path):
    p = tmp_path / "pool.md"
    p.write_text(SOUND_MD + "\n" + UNSOUND_MD, encoding="utf-8")
    sound, unsound = ic.ingest(p)
    assert [c["kind"] for c in sound] == ["sample_add"]
    assert [c["kind"] for c in unsound] == ["not_a_bug"]
