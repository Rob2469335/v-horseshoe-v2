# F2-S8 Host Execution Boundary

**Status:** the code-level guard is implemented; the **identity boundary is NOT
provisioned**. The guard is a fail-safe, not an established boundary.

This document is the target of the refusal message raised by
`PrivilegedIdentityError` in `qwen_train/f2_s8_acquisition.py`.

---

## 1. The problem this exists to prevent

Several stages execute code the project does **not** control:

| Stage | What runs repository-controlled code |
|---|---|
| Build | `pip install -e <repo>` executes the project's **build backend** (`setup.py`, `pyproject` hooks, `_custom_build/backend.py`) |
| Pre-check | `pytest --collect-only` **imports** `conftest.py` and every test module |
| Dry run | the declared test command runs the task's tests |

Measured evidence: a Pillow build failure produced a host-side traceback at
`C:\f2_pilot\python-pillow__Pillow-9105\repo\_custom_build\backend.py:32`,
proving repository code ran on the host, under `rob3\rober`, an account in
`BUILTIN\Administrators`.

Only base/gold **scored evaluation** runs inside `F2-S8-VM1` (PowerShell Direct /
VMBus, operator decision D5). Build and collection are **not** guest-isolated.

## 2. The guard

`qwen_train/f2_s8_acquisition.py`:

- `effective_identity_is_admin()` — reads the **current process token** via
  `whoami /groups` (a child inherits this process's token), interprets
  `S-1-5-32-544` (`BUILTIN\Administrators`), treats *"Group used for deny only"*
  as **not** privileged, and **fails closed** on any error, non-zero exit,
  unreadable attributes, or an unsupported platform.
- `require_unprivileged_identity(entry)` — raises `PrivilegedIdentityError`.
  There is **no environment override**; an override would reinstate the hole.

Call sites: `f2_s8_pilot_build.main/build`, `f2_s8_precheck.main/run_precheck`,
`f2_s8_pilot_dryrun.main/run_test`.

## 3. What the guard does and does not do

**Does:** refuse to start a stage that would execute untrusted code under an
administrator identity. This is fail-closed: the pipeline stops.

**Does not:** create an unprivileged identity, restrict filesystem access, or
establish isolation. Until the boundary below is provisioned and verified, no
build, collection or dry run should be run.

## 4. The boundary to be provisioned (REQUIRES OWNER AUTHORIZATION)

A dedicated local account `f2builder`:

| Requirement | Detail |
|---|---|
| Membership | **Users only** — never `Administrators`, never `Hyper-V Administrators` |
| Writable | `C:\f2_pilot` and per-worker cache roots `C:\f2_pilot\_cache\w<N>` |
| Denied (per-path, narrow) | `C:\Users\rober\F2_EVIDENCE`, the trust root, `.env`, the protected conversion artifacts |
| Hyper-V | no management reach |
| Network | package installation only (PyPI); whether a proxy is required is **not verified** |
| Handoff | the privileged driver spawns the worker **as `f2builder`** (`Start-Process -Credential`), before any task code runs |
| Artifact validation | the worker writes only a build manifest into a staging directory; the privileged process re-digests and validates it before importing into `F2_EVIDENCE` |

**Verification (not yet performed):** `whoami /groups` shows no admin SID; a probe
write into `F2_EVIDENCE` is denied; the guard passes; the manifest digest matches
on both sides.

**Rollback:** restore ACLs captured with `icacls <path> /save` before the change,
then delete the account.

## 5. Known limitations

- **Shared kernel.** An unprivileged account still shares the filesystem and
  kernel. This removes *custody* of evidence, ledgers, trust root and Hyper-V; it
  does not remove the possibility of host compromise. If local privilege
  escalation is in the threat model, a separate build VM is required.
- **Per-function guard.** A future function that launches pip/pytest without
  calling the guard would bypass it. A central launch chokepoint
  (`f2_untrusted_exec.run_untrusted`) is designed but not implemented; the
  external driver (`bulk2.py`, `durable.ps1`) is outside version control.
- **Other workflows are unguarded.** `build_swe_pool.py`, `run_repair_task.py`
  and `mine_fix_commits.py` also build and test repositories on this host and do
  **not** call the guard.
- **Guest egress is unverified.** D5 authorises a *transport*, not an offline
  posture.
