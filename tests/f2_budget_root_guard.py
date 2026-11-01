"""TEST-ONLY guard: the F2 budget store root must never be production.

``qwen_train.f2_authz_budget.budget_root()`` falls back to the **module
constants** ``PRODUCTION_BUDGET_ROOT`` / ``PRODUCTION_PROBE_BUDGET_ROOT``
whenever a caller omits its ``ledger_root`` argument. There is no environment
variable and no config key: the default is the constant, or ``None`` (fail
closed). Those constants point at the owner's real stores
(``C:\\Users\\rober\\F2_AUTHZ\\budget_v2`` and ``...\\probe_budget_v2``).

That is how fixture reservations ended up in the production probe store: the
23 directories under ``C:\\Users\\rober\\F2_AUTHZ\\probe_budget_v2`` all carry
``stage=S8-001``, ``operation=build_probe``, the testkit revision ``eeee...``
and the fixture task ids ``task-A``/``task-B``/``acme__widget-1``/
``acme__gadget-2``, written 2026-10-25 00:18-00:31 by a test revision that let
a caller default to the production root
(``s8_drafts/ISOLATION_ANALYSIS_2026-11-01.md`` §3.5).

Two layers make that unrepeatable, and this module is the second one:

1. ``tests/conftest.py``'s autouse ``isolate_f2_budget_roots`` redirects both
   constants to a temporary directory and wraps ``budget_root()`` so a
   production resolution fails the test that made it.
2. :func:`assert_isolated_from_production` is the single predicate both the
   wrapper and ``tests/test_f2_budget_root_isolation.py`` use, so the guard is
   testable in isolation (revert-then-pass: un-redirect the constant and the
   guard must fail).

``TRUE_*`` are captured at **import** time, which happens during collection,
before any autouse fixture can redirect the live constants. Tests that pin the
production default compare against these, never against the redirected
module attribute - otherwise the pin would silently follow the redirect.
"""

from __future__ import annotations

import os
import pathlib

from qwen_train import f2_authz_budget as budget

#: The owner's real budget stores, captured before any fixture can redirect them.
TRUE_PRODUCTION_BUDGET_ROOT = budget.PRODUCTION_BUDGET_ROOT
TRUE_PRODUCTION_PROBE_BUDGET_ROOT = budget.PRODUCTION_PROBE_BUDGET_ROOT

#: The literal paths the production defaults must be. A change here is a
#: production-store relocation, which is an owner decision, not a test change.
PRODUCTION_BUDGET_LITERAL = pathlib.Path(r"C:\Users\rober\F2_AUTHZ\budget_v2")
PRODUCTION_PROBE_LITERAL = pathlib.Path(r"C:\Users\rober\F2_AUTHZ\probe_budget_v2")

_PRODUCTION = tuple(
    p
    for p in (TRUE_PRODUCTION_BUDGET_ROOT, TRUE_PRODUCTION_PROBE_BUDGET_ROOT)
    if p is not None
)


def _norm(root: object) -> str:
    return os.path.normcase(os.path.abspath(str(root)))


def is_production_root(root: object) -> bool:
    """True when ``root`` is (or is a parent of / inside) a production store."""
    if root is None:
        return False
    candidate = _norm(root)
    for prod in _PRODUCTION:
        prod_norm = _norm(prod)
        if candidate == prod_norm:
            return True
        if candidate.startswith(prod_norm + os.sep):
            return True
    return False


def assert_isolated_from_production(root: object) -> None:
    """Fail loudly if a test resolved the owner's real F2 budget store."""
    if is_production_root(root):
        raise AssertionError(
            "a test resolved the PRODUCTION F2 budget root "
            f"{root!r}; pass ledger_root=/budget_ledger_root= (a tmp_path) "
            "instead of relying on the production default"
        )
