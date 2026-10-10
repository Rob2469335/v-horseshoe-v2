# F2 OS Runtime Decision Memo (S8 + T/X)

**Status: DECISION DEFERRED — `NOT ESTABLISHED`.** This memo compares three OS
options for the F2 S8 evidence runtime and the T/X arm runtime. It selects
nothing, provisions nothing, and changes no VM, activation, licence, firewall or
security control. **It is not authorization to run F2, to run a task, or to run
the five-task feasibility test.**

**Date:** 2026-10-10 · **Repository:** `95b30dd8` baseline + the commits named in
§6 · **Method:** repository inspection + static metadata analysis + primary
external sources (§7). No task command was executed at any point; every
`test_cmd` below was read as inert text.

---

## 1. Options

| Option | S8 runtime | T/X runtime |
|---|---|---|
| **A** | Windows | Windows |
| **B** | Linux | Linux |
| **C** | Linux | Windows |

---

## 2. §5.1 — Actual repository surfaces affected

From direct file inspection (paths repo-relative; the note is what is
OS-sensitive, not a judgement about quality).

| Surface | Path | OS sensitivity | Class |
|---|---|---|---|
| Task execution / venv | `qwen_train/swe_rebench_probe.py` | `venv / "Scripts" / "python.exe"` (L282) and `_run(["cmd","/c",step])` (L309) — neither construct exists on Linux | **Proven incompatibility (Linux)** |
| Pool builder | `qwen_train/build_swe_pool.py` | same two constructs (L177, L197) | Proven incompatibility (Linux) |
| Baseline / twine eval | `qwen_train/cli_baseline_swe.py:306`, `eval_twine.py:100`, `run_twine_eval.py:53` | `".venv"/"Scripts"/"python.exe"` | Proven incompatibility (Linux) |
| Repair runner | `qwen_train/run_repair_task.py:647,711` | `Path(sys.executable).parent.parent / "Scripts" / "python.exe"`; argv list (no `shell=True`) at L726 | Proven incompatibility (Linux) |
| Backend launcher | `start-dev.ps1`, `lifecycle.ps1`, `unified-start.ps1`, `unified-stop.ps1`, `launch_backend.ps1` | PowerShell-only; `$root = "C:\Users\rober\Projects\v-horseshoe-v2"`; `Get-NetTCPConnection` | Proven incompatibility (Linux) |
| Self-healing relaunch | `swarm_os/healing/recovery_engine.py:130-140` | re-invokes `powershell -ExecutionPolicy Bypass -File start-dev.ps1`; `start_new_session=True` | Proven incompatibility (Linux) |
| Model router | `swarm_os/.../model_router.py:204,222` | `Popen(["cmd.exe","/c","launch_llama.bat"])`, `proc.kill()` | Proven incompatibility (Linux) |
| F2 isolation boundary | `qwen_train/f2_vm_provision.ps1`, `f2_vm_rollback.ps1`, `f2_input_bundle.ps1` | Hyper-V only: `New-VMSwitch`, `Set-VMFirmware`, `Add-VMNetworkAdapterExtendedAcl`, `New-VHD`, `Mount-VHD`, `Format-Volume -FileSystem NTFS` | Proven Windows-only |
| Isolation verdict logic | `qwen_train/f2_isolation.py:226,490-533` | documents Windows Firewall semantics: loopback not filtered; `-Program` rules do not inherit to children; WFP errno 10013/10054 | Proven Windows-specific semantics |
| Model gateway | `qwen_train/f2_model_gateway.py:178` | binds only the Hyper-V internal-switch host address | Windows/VM-coupled |
| S8 input transport | `qwen_train/f2_input_bundle.ps1` | VHDX create/mount/NTFS format — 100 % Windows storage stack | Proven Windows-only |
| S8 record channel | `qwen_train/f2_s8_vhdx.py`, `f2_s8_record.py` | pure Python; VHDX is a Microsoft container format but the reader is format-level, not OS-level | OS-neutral |
| Execution adapter | `qwen_train/f2_execution_adapter.py:567-624,1132` | `CREATE_NO_WINDOW`, psutil process-tree teardown, `tempfile.mkdtemp` | Behaviour-sensitive |
| Arm worker | `qwen_train/f2_arm_worker.py:1055` | `subprocess.run(exec_cmd, shell=True, timeout=30)` — shell semantics differ per OS | Behaviour-sensitive |
| Evaluator | `qwen_train/f2_evaluator.py:148-149,326-343` | node-id normalisation strips `\`; appends `--junitxml=<path>` | OS-neutral apart from path style |
| Agent shell tool | `swarm_os/capabilities/sandbox_repl.py:176-309` | `bash`/`sh`/`shell` all dispatch to **`pwsh`**; comment notes grep/head/tail/sed/awk are absent | Windows-shaped contract |
| Agent tool spawning | `runtime_v2/services/tool_executor.py:65-83,1055-1074,1322-1333` | `create_subprocess_exec` + `proc.kill()`; metacharacter denylist justified by **cmd.exe and `npx.cmd` shims** | Windows-shaped |
| Host introspection | `runtime_v2/services/system_intel.py:61,362-430` | `winreg`, `HKEY_LOCAL_MACHINE`, `CREATE_NO_WINDOW` | Windows-only |
| Console / notifications | `organism_console/cli.py:25,320`, `notifications.py:48,68` | `sys.platform == "win32"` branches | Behaviour-sensitive |
| Path validator | `runtime_v2/services/task_readiness.py` | `_UNSAFE_PATH_CHARS` = Windows-forbidden filename chars + shell separators; `\` normalised to `/` | Windows-derived rule |
| Evidence containment | `qwen_train/f2_evidence.py:447-465` | explicitly handles the Windows-drive case because `Path().is_absolute()` is False on POSIX | already cross-OS |
| Artifact store | `qwen_train/f2_governance.py:389-414` | `Path.is_relative_to` containment — OS-neutral | OS-neutral |
| File permissions | *(none found)* | no `os.chmod`/`os.umask`/`fcntl` in production code | no migration surface |
| Linux-only assumptions | *(none found)* | no `os.getuid`, no shebang-driven execution, no `/tmp` writes in production code | no migration surface |
| Container dependency | `docker-compose.yml` (Qdrant) | Qdrant container; task code is deliberately **docker-free** (`build_swe_pool.py:289`) | portable |
| Platform-gated tests | `tests/test_pid_lifecycle.py`, `test_sandbox_shell.py`, `test_system_intel.py`, `test_screen_control.py`, `test_system_healing.py`, `test_f2_op_infra_004.py` | all `sys.platform != "win32"` skip markers | test-only |

**Count of proven Windows-only or Windows-shaped surfaces: 14.**
**Count of proven Linux-only surfaces: 0.**

Interpretation: the repository is *Windows-shaped*, not merely Windows-hosted.
That is a migration-cost fact, **not** evidence that Windows is the right
scientific runtime for the target repositories.

---

## 3. §5.2 — Licensing and evaluation limits

### 3.1 What the official sources say (fetched during this task)

| Source | Publisher | URL | Date shown | What it supports | What it does **not** establish |
|---|---|---|---|---|---|
| *Windows 11 Enterprise \| Microsoft Evaluation Center* | Microsoft | `https://www.microsoft.com/en-us/evalcenter/evaluate-windows-11-enterprise` | no date shown | **90-day evaluation**; "A product key is not required"; on expiry "the desktop background will turn black … the PC will shut down every hour"; offered editions incl. *Windows 11 Enterprise, version 26H2* and *Windows 11 Enterprise LTSC 2024* | any licence clause restricting use to evaluation, any production-use prohibition, any statement about this host's VM |
| *Windows Server 2025 \| Microsoft Evaluation Center* | Microsoft | `…/evaluate-windows-server-2025` | no date shown | contrast case: **180 days**, 10-day activation window | anything about client Windows |
| *What version of Windows am I running?* | Microsoft Learn | `…/client-tools/windows-version-search` | 2026-08-24 | `winver`, Settings ▸ About, `systeminfo`, **`slmgr /dlv`** show edition/licensing | that an edition string encodes evaluation status |
| *Get-ComputerInfo* | Microsoft Learn PowerShell | `…/powershell/module/microsoft.powershell.management/get-computerinfo` | 2025-07-24 | cmdlet exists (alias `gin`) for reading OS properties from inside a guest | the `WindowsEditionID` property name itself |
| *Upgrade Windows Server in Place* | Microsoft Learn | `…/windows-server/get-started/upgrade-in-place` | 2026-04-28 | **`Get-ComputerInfo -Property WindowsBuildLabEx,WindowsEditionID`** and registry `EditionID` at `HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion` | anything about evaluation expiry |
| *[MS-VHDX]: Virtual Hard Disk v2 (VHDX) File Format* | Microsoft Learn (Open Specifications) | `…/openspecs/windows_protocols/ms-vhdx/` | updated 2024-10-30; published version 4/23/2024 (Protocol Revision 8.0) | identity and published version of the specification | file-layout detail (landing page only) |

**Fetch outcomes recorded verbatim.** The URL supplied in the task prompt,
`…/ms-vhdx/83f6b700-89f9-4aeb-9773-5b1e6a9c5f1f`, returned **HTTP 404**; the
canonical landing page is `…/openspecs/windows_protocols/ms-vhdx/` (canonical
section URL `…/83e061f8-f6e2-4de1-91bd-5d518a43d477`). The Windows Server
storage page `…/windows-server/storage/vhd-vhdx` and the Evaluation Center FAQ
`…/evalcenter/faq` also returned **404**. Microsoft Product Terms
(`microsoft.com/licensing/terms/`) loaded but is a JavaScript navigation shell
with no evaluation entry reachable by fetch.

**NOT ESTABLISHED:** the full evaluation *licence terms* text (usage scope,
redistribution, benchmark clauses). No fetched Microsoft page supplied it.

### 3.2 The existing VM's edition

**NOT ESTABLISHED, and deliberately not probed.** No command was run against
`F2-Isolation-VM` for this memo (§0.4 forbids touching the VM, and the edition
was not needed to make the decision). What the repository already records:
`docs/EXPERIMENT_J_F2_ORCHESTRATOR_IMPLEMENTATION_AUTHORIZATION.md` (F2-IMPL-AUTH-032)
states SecureBoot On, vTPM on, internal-only NIC, guest Heartbeat OK — and says
**nothing** about edition, installation date, activation state or expiry.

**The exact read-only commands an operator may run later** (from the official
sources above), inside the guest:

```powershell
Get-ComputerInfo -Property WindowsProductName,WindowsEditionID,BuildLabEx
slmgr /dlv          # licensing detail; never share a product key if one appears
winver              # edition + version on screen
```

Whether `WindowsEditionID` encodes evaluation status (e.g. an `…Eval` suffix)
is **NOT ESTABLISHED** from official documentation.

### 3.3 Comparison

| Factor | Windows (A/C) | Linux (B) |
|---|---|---|
| Evaluation duration | **90 days** (official), desktop blackens and **the PC shuts down hourly** after expiry | n/a for a typical distro; package repos are versioned |
| Reproducibility after expiry | **At risk**: an expired evaluation self-disables hourly — a 300-pair confirmatory run cannot be planned against that clock | Generally restorable from pinned images/packages |
| Licensed alternative | Required if the run outlives the evaluation — a purchase or volume-licence decision the operator must make | Distro licensing is not the constraint |
| Maintenance burden | Windows patching + Hyper-V + PowerShell-only launchers | image/package pinning; no launcher rewrite **for Linux-only code**, but this repo's launchers are all `.ps1` |
| Runtime parity with target repos | **Lower**: 10,877 of 33,770 accepted tasks (32.21 %) carry POSIX-only syntax markers in `test_cmd` | **Higher** on that same static evidence |
| Migration effort for *this* repo | none | **high** — see §2 (14 Windows-shaped surfaces, 0 Linux-only) |

---

## 4. §5.3 — Static task-compatibility census

**Method.** Read-only inspection of the pinned acquisitions
(`swe-bench-live` `b51a8642…`, `swe-rebench-v2` `10483de0…`) joined to the pinned
union report. `test_cmd` was classified by **text markers only**
(`powershell|pwsh|cmd /c|findstr|.ps1|where|py -3|.bat|.cmd|choco|\\` for
Windows; `bash|sh -c|#!|apt|yum|dnf|chmod|./script|set -e|export|source|make|
cmake|tox|nox|linux` for POSIX). **No task command was executed, no dependency
installed, no repository cloned, no image built, no test run.**

**Denominator: 33,770 accepted rows** (the pinned union report). Second
denominator: **9,268 metadata-eligible rows**.

| Label | of 33,770 accepted | % | of 9,268 eligible | % |
|---|---|---|---|---|
| Both-plausible (no OS marker at all) | **22,893** | 67.79 % | **7,008** | 75.62 % |
| Linux-plausible (POSIX markers only) | **10,877** | 32.21 % | **2,260** | 24.38 % |
| Windows-plausible | **0** | 0.00 % | **0** | 0.00 % |
| Ambiguous (both marker sets) | **0** | 0.00 % | **0** | 0.00 % |
| Not classifiable (empty `test_cmd`) | **0** | 0.00 % | **0** | 0.00 % |
| Rows with no metadata at all | 0 | — | — | — |

**Additional static facts.** `docker` appears in **12** of 33,770 `test_cmd`
values (0.04 %). Declared `install_config.base_image_name` values are
container-shaped and non-portable by construction — top: `python_base_310`
6,028, `node_16` 5,585, `go_1.19.13` 2,953, `rust_1.84` 2,872, `node_20` 2,757,
`go_1.23.8` 1,788, `python_base_37` 1,215, `java_21` 1,004, `php_8.3.16` 1,109.
Language mix (accepted): python 7,048, go 6,144, js 4,138, ts 4,204, rust 3,123,
java 1,716, php 1,445, kotlin 889, julia 793, elixir 416, scala 411, swift 362,
dart 251, c 230, cpp 182, r 157, clojure 105, csharp 173, lua 39, ocaml 58,
`<none>` 1,886 (the SWE-bench-Live rows carry no `language` field).

**Limitations — these labels are hypotheses about syntax, not results.**

* The marker sets are deliberately conservative, so "both-plausible" means
  *no OS signal was found*, not *proven to run on both*.
* Zero Windows-plausible rows is a statement about my marker list as much as
  about the data: it says no `test_cmd` contains Windows shell syntax. It does
  **not** say the tasks run on Windows.
* Nothing here measures dependency installation, native toolchains, case-sensitive
  filesystems, path-length limits, or test-framework behaviour — all of which
  can fail on one OS and not the other.
* `install_config` describes the **source project's Docker image**, not the
  host: it is evidence about how the benchmark was built, not about our runtime.

**Evidence class: `SUPPORTED` (static plausibility only).**

---

## 5. §5.4 — Cheapest five-task native-Windows feasibility test (**PLAN ONLY — NOT EXECUTED**)

**Purpose:** decide whether Option A is viable before any migration cost is
incurred. Nothing below has been run, and running it requires a separate,
explicit authorization.

**Reproducible selection rule** (applied to the pinned pre-registration order,
`data/f2_population/ordering/`, rule `created_at_desc_instance_id_asc_v1`,
content digest `62bf22ff…`):

> Take the **first** metadata-eligible task, in registered order, in **each** of
> five materially different runtime/dependency profiles; skip a profile only if
> no eligible task carries it, and record the skip.

Profiles: (P1) CPython + `python_base_310`; (P2) Node + `node_16`/`node_20`;
(P3) Go; (P4) Rust; (P5) a compiled/JVM-or-.NET toolchain (`java_21`,
`kotlin`, or `csharp`). This is deliberately *not* "the five easiest tasks":
it covers interpreted, JIT, compiled, and managed toolchains, and it is
deterministic — anyone re-running the rule on the same pinned inputs gets the
same five ids.

**Required measurements per task:** wall clock for clone, environment creation
and dependency install; peak working-set and disk for the environment; whether
`test_cmd` parses under `cmd`/`pwsh` without rewriting; whether `FAIL_TO_PASS`
and `PASS_TO_PASS` node ids resolve under the Windows test runner; JUnit
produced and parseable by `qwen_train/f2_evaluator.py`; and the failure mode if
it fails (syntax, path, case-sensitivity, native toolchain, dependency
resolution, test-framework, timeout).

**Success criterion:** ≥ 4 of 5 tasks reach a parsed JUnit report with
resolvable node ids, and every failure is classifiable without rewriting the
task. **Failure criterion:** ≥ 2 tasks fail for OS-specific reasons that cannot
be addressed without altering task content (which the protocol forbids).

**What five tasks cannot establish:** no rate, no confidence interval, no
statement about the other 9,263 metadata-eligible tasks, no statistical power,
no proof of isolation, and no scientific conclusion about T vs X. Five is a
*feasibility probe*, not a sample.

**Required human authorization to run it (not held):** an operator instruction
naming (a) that the test may execute real task code, (b) the execution
environment — an appropriately isolated guest, not the host (§0.4.5/0.4.8), (c)
the exact five ids produced by the rule above, (d) who may read the results.

---

## 6. §5.5 — OS decision

**DECISION: UNDECIDED. Recommend remaining undecided until the separately
authorized five-task Windows feasibility test reports.**

Reasoning, in order:

1. **Evidence for Windows is a migration-cost argument, not a correctness
   argument.** 14 proven Windows-shaped surfaces vs 0 Linux-only surfaces
   (§2) means Option B/C carries real, enumerable rework. That is the only
   thing the repository evidence establishes.
2. **Evidence for Linux is a compatibility argument, not a cost argument.**
   32.21 % of accepted `test_cmd` values carry POSIX-only markers and **0**
   carry Windows markers (§4), and the source projects' own base images are
   container-shaped. That is `SUPPORTED`, not proof.
3. **Evaluation risk is real and currently unresolved.** Windows 11 Enterprise
   Evaluation is 90 days and self-disables hourly on expiry (§3.1), and the
   existing VM's edition/activation/expiry is **NOT ESTABLISHED** (§3.2). A
   300-pair confirmatory design cannot be scheduled against an unknown clock.
   This does not block the feasibility test; it blocks *planning the campaign*.
4. **Option C is not ruled out, but it is not available either.** It would
   require the Linux-S8 ↔ Windows-T/X evidence-transfer boundary to preserve
   task identity, evidence provenance and the frozen protocol. The disk-mediated
   channel in `qwen_train/f2_s8_record.py` / `f2_s8_channel.py` is the *shape* of
   that boundary, but it has **not** been exercised across an OS boundary and
   its transport is explicitly labelled *"Reader mechanics; hostile
   guest-written disk not tested live."* **Do not assume cross-OS equivalence.**
5. If the feasibility test succeeds, evaluate **Option A** first (lowest
   migration cost), conditional on the licensing clock being settled (§3). If it
   fails materially, evaluate **Option B** using §2 as the concrete migration
   inventory — that inventory is complete enough to cost.

---

## 7. Sources consulted in preparing this memo

Repository (read directly): `AGENTS.md`; `docs/EXPERIMENT_J_F2_OPERATOR_HANDOFF.md`;
`docs/EXPERIMENT_J_F2_READINESS_AND_STATISTICAL_PLAN.md` §4c–4e;
`docs/EXPERIMENT_J_F2_ORCHESTRATOR_IMPLEMENTATION_AUTHORIZATION.md` (AUTH-012,
AUTH-013, AUTH-014, AUTH-028, AUTH-029, AUTH-031, AUTH-032);
`docs/EXPERIMENT_J.md` (F0); the modules named in §2; the pinned acquisitions and
union report named in §4.

External (fetched during this task, outcomes in §3.1): Microsoft Evaluation
Center — *Windows 11 Enterprise*, *Windows Server 2025*; Microsoft Learn —
*[MS-VHDX] Virtual Hard Disk v2 (VHDX) File Format* landing page and its
*Layout*, *Header Section*, *File Type Identifier*, *BAT*, *Payload BAT Entry
States*, *Blocks*, *File Parameters*, *Virtual Disk Size*, *Logical Sector Size*
sections; *What version of Windows am I running?*; *Get-ComputerInfo*; *Upgrade
Windows Server in Place*; Microsoft Product Terms (navigation only).
