"""Guard: no test may resolve - or write - the PRODUCTION F2 budget store.

`qwen_train.f2_authz_budget.budget_root()` defaults to the module constants
`PRODUCTION_BUDGET_ROOT` / `PRODUCTION_PROBE_BUDGET_ROOT` when a caller omits
`ledger_root`; no environment variable or config key selects it. The 23
directories in `C:\\Users\\rober\\F2_AUTHZ\\probe_budget_v2` are fixture
reservations that took that default (`stage=S8-001`, `operation=build_probe`,
testkit revision `eeee...`, fixture task ids), so the default itself is the
defect surface.

Three checks, each failing loudly rather than silently:

1. the live constants are redirected away from production for every test;
2. a real `reserve()` with no `ledger_root` writes into the temporary root;
3. if a test restores a production constant, `budget_root()` refuses - which is
   the revert-then-pass proof that the guard is not inert.

Plus a static scan of the suite's own call sites, so a future test that omits
its root is caught at review time instead of at the owner's store.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from qwen_train import f2_authz_budget as B

from tests import f2_budget_root_guard as guard

_TESTS_DIR = pathlib.Path(__file__).resolve().parent
_BUDGET_ALIASES = {"B", "BUDGET", "budget"}
_STORE_CALLS = {"reserve", "claim_retry", "remaining", "audit"}
#: Calls whose `ledger_root` is the second POSITIONAL parameter (`audit`,
#: `remaining`); `reserve`/`claim_retry` take it keyword-only.
_POSITIONAL_ROOT = {"audit", "remaining"}


def _grant_record() -> dict:
    return {
        "id": "budget-isolation-guard",
        "schema": "f2-authorization/v2",
        "stage": "S8-001",
        "operations": ["build_probe"],
        "target_credited": 0,
        "awards_credits": False,
        "not_before": "2020-01-01T00:00:00+00:00",
        "not_after": "2099-01-01T00:00:00+00:00",
        "budget": {"max_executions": 2, "max_total_tasks": 4, "max_retries": 1},
    }


def test_default_resolution_is_redirected_off_production() -> None:
    """The autouse isolation fixture redirects both default roots."""
    for is_probe in (False, True):
        resolved = B.budget_root(None, is_probe=is_probe)
        assert not guard.is_production_root(resolved), (
            f"budget_root(is_probe={is_probe}) resolved the production store "
            f"{resolved!r}"
        )
    assert B.budget_root(None) != B.budget_root(None, is_probe=True)


def test_a_write_with_the_default_root_lands_in_the_temporary_root() -> None:
    """A reservation that omits `ledger_root` must not touch production."""
    entry = B.reserve(
        record=_grant_record(),
        binding={"repository_revision": "e" * 40},
        binding_digest="0" * 64,
        stage="S8-001",
        operation="build_probe",
        execution_id="budget-isolation-guard",
        task_count=1,
    )
    digest = entry["record_digest"]
    resolved = B.budget_root(None, is_probe=True)
    written = pathlib.Path(resolved) / digest
    assert written.is_dir(), "the reservation did not land in the resolved root"
    assert not guard.is_production_root(written)
    assert not guard.is_production_root(pathlib.Path(resolved))


def test_the_guard_refuses_a_restored_production_root(monkeypatch) -> None:
    """Revert-then-pass: un-redirect the constant and resolution must fail."""
    monkeypatch.setattr(
        B, "PRODUCTION_PROBE_BUDGET_ROOT", guard.PRODUCTION_PROBE_LITERAL
    )
    with pytest.raises(AssertionError, match="PRODUCTION F2 budget root"):
        B.budget_root(None, is_probe=True)

    monkeypatch.setattr(B, "PRODUCTION_BUDGET_ROOT", guard.PRODUCTION_BUDGET_LITERAL)
    with pytest.raises(AssertionError, match="PRODUCTION F2 budget root"):
        B.budget_root(None)


def test_the_predicate_recognises_nested_production_paths(tmp_path) -> None:
    assert guard.is_production_root(guard.PRODUCTION_BUDGET_LITERAL)
    assert guard.is_production_root(guard.PRODUCTION_PROBE_LITERAL / "deadbeef")
    assert not guard.is_production_root(tmp_path)
    assert not guard.is_production_root(tmp_path / "budget_v2")
    assert not guard.is_production_root(None)


def test_no_budget_store_call_site_omits_its_root() -> None:
    """Static scan: every `B.<store call>` in tests/ passes its root.

    `reserve`/`claim_retry` take `ledger_root` as a keyword-only argument;
    `audit`/`remaining` accept it positionally as the second argument. Calls
    that forward ``**kwargs`` are exempt (the helper resolves the root). This
    file is exempt: the write test above deliberately omits the argument to
    prove the redirected default is used.
    """
    offenders: list[str] = []
    for path in sorted(_TESTS_DIR.glob("test_*.py")):
        if path.name == pathlib.Path(__file__).name:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr not in _STORE_CALLS:
                continue
            if not isinstance(func.value, ast.Name):
                continue
            if func.value.id not in _BUDGET_ALIASES:
                continue
            if any(kw.arg == "ledger_root" for kw in node.keywords):
                continue
            if any(kw.arg is None for kw in node.keywords):  # **kwargs forwarding
                continue
            if func.attr in _POSITIONAL_ROOT and len(node.args) >= 2:
                continue
            offenders.append(f"{path.name}:{node.lineno}: B.{func.attr}()")
    assert not offenders, (
        "these test call sites rely on the production budget root default:\n"
        + "\n".join(offenders)
    )
