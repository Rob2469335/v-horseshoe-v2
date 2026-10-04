"""Experiment-J learning-actor environment membrane (D-12 / R4).

The security hole this suite closes
-----------------------------------
``docs/LEARNING_EXPERIMENT_STATE.md`` D-12 recorded a ``GOVERNANCE GAP``: the
untrusted-subprocess environment builder stripped variable names containing
``TOKEN``/``SECRET``/``PASSWORD``/``PASSWD`` but **not** a bare ``KEY``, so
``MY_KEY``, ``SIGNING_KEY`` and ``SSH_KEY`` reached the shell that the learning
actor runs. Because a learning run can execute shell commands, MCP-tool denial
alone did **not** establish that a learning event was network-free or
credential-free.

Two independent properties are proved here:

1. ``is_credential_env_name`` closes the ``KEY`` gap by whole-segment matching
   while NOT over-stripping innocent names that merely contain those letters.
2. ``clean_learning_env`` is ALLOWLIST-based, so a credential whose name nobody
   anticipated still cannot reach the learning shell.

No subprocess is spawned and no experiment is executed.
"""
from __future__ import annotations

import pytest

from swarm_os.services.security_gate import (
    _ENV_ALWAYS_KEEP,
    clean_learning_env,
    clean_sandbox_env,
    is_credential_env_name,
)

# Names D-12 proved survived the OLD substring-only denylist.
D12_PROVEN_LEAKS = (
    "MY_KEY",
    "SIGNING_KEY",
    "SSH_KEY",
    "KEY",
    "SOME_RANDOM_KEY",
    "ENCRYPTION_KEY",
    "ACCESS_KEY",
    "SESSION_KEY",
)

# Additional realistic credential shapes not covered by D-12.
OTHER_CREDENTIALS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_ACCESS_KEY_ID",
    "GITHUB_TOKEN",
    "GITHUB_PAT",
    "DB_PASSWORD",
    "MY_SECRET",
    "SESSION_TOKEN",
    "PRIVATE_KEY",
    "DATABASE_URL",
    "REDIS_DSN",
    "AWS_SESSION_TOKEN",
)


@pytest.mark.parametrize("name", D12_PROVEN_LEAKS)
def test_d12_proven_leaks_are_now_credentials(name):
    """The exact variables D-12 proved leaked must now be classified."""
    assert is_credential_env_name(name) is True


@pytest.mark.parametrize("name", OTHER_CREDENTIALS)
def test_other_credential_shapes_are_detected(name):
    assert is_credential_env_name(name) is True


@pytest.mark.parametrize(
    "name",
    [
        # Names that CONTAIN key-ish letters but are whole segments of ordinary
        # words. Over-stripping these would break legitimate runtime, so the
        # matcher must be segment-exact, not substring.
        "MONKEY_COUNT",
        "KEYBOARD_LAYOUT",
        "MONKEYS",
        "SESSIONS_MAX",
        "AUTHORS_FILE",
        "TOKENS_PER_MINUTE_BUDGET_NOT_A_SECRET",
    ],
)
def test_innocent_names_are_not_over_stripped(name):
    """Segment-exact matching avoids collateral damage on ordinary names."""
    if name == "TOKENS_PER_MINUTE_BUDGET_NOT_A_SECRET":
        # "TOKENS" IS a credential marker as a whole segment; this documents the
        # deliberate trade-off rather than asserting a false negative.
        assert is_credential_env_name(name) is True
        return
    assert is_credential_env_name(name) is False


def test_d12_leaks_do_not_survive_into_subprocess_env(monkeypatch):
    """End-to-end: none of the D-12 leaks may reach the built env."""
    for name in D12_PROVEN_LEAKS + OTHER_CREDENTIALS:
        monkeypatch.setenv(name, "sentinel-value")
    env = clean_sandbox_env()
    leaked = sorted(n for n in env if is_credential_env_name(n))
    assert leaked == [], f"credential-named variables survived: {leaked}"
    for name in D12_PROVEN_LEAKS:
        assert name not in env


def test_swarm_feature_gates_are_stripped(monkeypatch):
    """SWARM_* is stripped wholesale: it covers the receipt key and harness key."""
    monkeypatch.setenv("SWARM_RECEIPT_KEY", "sentinel")
    monkeypatch.setenv("SWARM_HARNESS_KEY", "sentinel")
    monkeypatch.setenv("SWARM_AUTONOMY", "0")
    env = clean_sandbox_env()
    for name in ("SWARM_RECEIPT_KEY", "SWARM_HARNESS_KEY", "SWARM_AUTONOMY"):
        assert name not in env


def test_learning_env_is_allowlist_based(monkeypatch):
    """An unanticipated credential name still cannot reach the learning shell."""
    # A name no denylist author would think of, carrying a secret VALUE.
    monkeypatch.setenv("TOTALLY_UNANTICIPATED_CREDENTIAL", "sentinel")
    monkeypatch.setenv("PATH", r"C:\Windows\system32")
    env = clean_learning_env()
    assert "TOTALLY_UNANTICIPATED_CREDENTIAL" not in env
    # ... and the interpreter is still reachable, or nothing could run at all.
    assert "PATH" in env
    assert env["PYTHONNOUSERSITE"] == "1"


def test_learning_env_keeps_windows_process_essentials(monkeypatch):
    """Windows cannot spawn a process without these; losing them breaks runs."""
    monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")
    monkeypatch.setenv("COMSPEC", r"C:\Windows\system32\cmd.exe")
    monkeypatch.setenv("TEMP", r"C:\Temp")
    env = clean_learning_env()
    for name in ("SYSTEMROOT", "COMSPEC", "TEMP", "PATH"):
        assert name in env, f"{name} is required to spawn a subprocess"


def test_learning_env_honours_explicit_allowlist(monkeypatch):
    monkeypatch.setenv("TASK_INTERPRETER", "py")
    monkeypatch.setenv("UNRELATED_VAR", "x")
    env = clean_learning_env(allowlist={"TASK_INTERPRETER"})
    assert "TASK_INTERPRETER" in env
    assert "UNRELATED_VAR" not in env


def test_learning_env_rejects_a_credential_even_if_allowlisted(monkeypatch):
    """An allowlist entry cannot smuggle a credential-named variable through."""
    monkeypatch.setenv("SIGNING_KEY", "sentinel")
    env = clean_learning_env(allowlist={"SIGNING_KEY"})
    assert "SIGNING_KEY" not in env


def test_extra_is_credential_filtered(monkeypatch):
    """`extra` is trusted for provenance but not for credential smuggling."""
    monkeypatch.delenv("MY_KEY", raising=False)
    env = clean_sandbox_env(extra={"MY_KEY": "sentinel", "SAFE_VAR": "ok"})
    assert "MY_KEY" not in env
    assert env["SAFE_VAR"] == "ok"


def test_always_keep_set_has_no_credential_name():
    """The always-keep list must itself be credential-free."""
    for name in _ENV_ALWAYS_KEEP:
        assert not is_credential_env_name(name), (
            f"{name} is in the always-keep list but looks like a credential"
        )