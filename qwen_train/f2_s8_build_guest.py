"""Guest-side build contract and pure validation helpers.

This module is deliberately **not connected to a real VM**.  It defines the
typed build request/result used by a future VM-contained builder and the
pure checks that can be exercised with synthetic transports and temporary
directories.  It never opens a socket, reads a credential, or runs a build.

The production builder must supply a real ``GuestTransport`` implementation;
until then every live path remains unavailable and fail-closed.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import re
import stat
import subprocess
import time
from dataclasses import dataclass, field
from typing import Iterable, Protocol, Sequence

TASK_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]+__[A-Za-z0-9_.\-]+-\d+$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GITHUB_REPO_RE = re.compile(
    r"^https://github\.com/[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+\.git$"
)

#: Build statuses returned by the guest builder.
STATUS_BUILT = "BUILT"
STATUS_BUILD_FAILURE = "BUILD_FAILURE"
STATUS_TIMEOUT = "TIMEOUT"
STATUS_INFRASTRUCTURE_FAILURE = "INFRASTRUCTURE_FAILURE"

#: Probe classifications the guest builder may emit.
CLASS_COMPATIBLE = "OBSERVED_COMPATIBLE"
CLASS_BUILD_FAILURE = "BUILD_FAILURE"


class BuildContractError(RuntimeError):
    """The build request or result violates the guest-build contract."""


class BuildPrerequisiteError(BuildContractError):
    """A required input/tool is missing before any build work starts."""


@dataclass(frozen=True)
class GuestCommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False


@dataclass(frozen=True)
class BuildSpec:
    """One build request executed entirely inside the guest.

    ``workspace`` and the patch path are guest-absolute paths.  The host-side
    precheck verifies the corresponding local inputs before transfer.

    Phase separation for ``CONTROLLED_ACQUISITION_OFFLINE_EXECUTION``:
    * If ``acquire_argv`` is non-empty, ``run_guest_build`` executes ``git clone``,
      ``git checkout``, ``git rev-parse``, and ``acquire_argv`` BEFORE applying
      ``test_patch``, and then requires ``isolate_network_before_build`` to
      disconnect all guest network adapters and verify that ``network_online()``
      is ``False`` before ``git apply``, ``install_argv``, and ``build_argv`` run.
    * If ``acquire_argv`` is empty under an offline-execution policy, network
      isolation is enforced and verified immediately after ``git rev-parse`` and
      before ``git apply``, ``install_argv``, and ``build_argv``.
    """

    task_id: str
    repo_url: str
    base_commit: str
    workspace: str
    test_patch: str
    test_patch_sha256: str
    install_argv: tuple[str, ...]
    build_argv: tuple[str, ...]
    acquire_argv: tuple[str, ...] = ()
    network_policy: str = "OFFLINE_WHEELHOUSE_ONLY"
    require_offline_build_phase: bool = False
    timeout_seconds: int = 3600
    disk_floor_bytes: int = 25_000_000_000
    per_task_disk_bytes: int = 3_500_000_000
    required_python: str = ""
    test_argv: tuple[str, ...] = ()


@dataclass(frozen=True)
class GuestBuildResult:
    task_id: str
    status: str
    classification: str
    workspace: str | None
    reason_code: str
    stdout_tail: str
    stderr_tail: str
    duration_seconds: float
    failure: str = ""
    network_isolated_for_build: bool = False
    phase_timings: dict[str, float] = field(default_factory=dict)
    phase_details: dict[str, dict[str, float]] = field(default_factory=dict)


def check_python_version_compatible(required: str, actual: str) -> bool:
    """Check if actual Python version satisfies required version specification."""
    if not required or not actual:
        return True
    req = required.strip()
    act = actual.strip()
    try:
        from packaging.specifiers import SpecifierSet
        from packaging.version import Version

        act_ver = Version(act)
        if re.fullmatch(r"^\d+\.\d+$", req):
            spec = SpecifierSet(f"=={req}.*")
        elif re.fullmatch(r"^\d+\.\d+\.\d+$", req):
            spec = SpecifierSet(f"=={req}")
        else:
            spec = SpecifierSet(req)
        return act_ver in spec
    except Exception:
        if req.startswith("=="):
            req = req[2:]
        return act.startswith(req)


class GuestTransport(Protocol):
    """The minimal guest operations a builder needs.

    A real implementation runs over the approved guest channel; tests use a
    deterministic fake.  No method may expose host credentials.
    """

    def run(
        self, argv: Sequence[str], *, cwd: str, timeout: int
    ) -> GuestCommandResult: ...

    def sha256(self, path: str) -> str: ...

    def free_bytes(self, path: str) -> int: ...


def _tail(text: str, limit: int = 4000) -> str:
    text = text or ""
    return text[-limit:]


def _failed(
    spec: BuildSpec,
    *,
    status: str,
    reason_code: str,
    started: float,
    stdout: str = "",
    stderr: str = "",
    clock=time.monotonic,
    phase_timings: dict[str, float] | None = None,
    phase_details: dict[str, dict[str, float]] | None = None,
) -> GuestBuildResult:
    try:
        now = clock()
    except (StopIteration, RuntimeError):
        now = started
    return GuestBuildResult(
        task_id=spec.task_id,
        status=status,
        classification=CLASS_BUILD_FAILURE,
        workspace=None,
        reason_code=reason_code,
        stdout_tail=_tail(stdout),
        stderr_tail=_tail(stderr),
        duration_seconds=max(0.0, now - started),
        failure="1",
        phase_timings=dict(phase_timings or {}),
        phase_details=dict(phase_details or {}),
    )


def _validate_spec(spec: BuildSpec) -> None:
    if not TASK_ID_RE.fullmatch(spec.task_id):
        raise BuildContractError(f"invalid task id: {spec.task_id!r}")
    if not GITHUB_REPO_RE.fullmatch(spec.repo_url):
        raise BuildContractError(f"repository URL is not an approved GitHub URL: {spec.repo_url!r}")
    if not COMMIT_RE.fullmatch(spec.base_commit):
        raise BuildContractError(f"base_commit is not a 40-hex commit: {spec.base_commit!r}")
    if not spec.workspace or not spec.test_patch:
        raise BuildContractError("workspace and test_patch are required")
    if not SHA256_RE.fullmatch(spec.test_patch_sha256):
        raise BuildContractError("test_patch_sha256 must be 64 lowercase hex")
    if not spec.install_argv or not spec.build_argv:
        raise BuildContractError("install_argv and build_argv are required")
    if spec.network_policy not in ALLOWED_BUILD_NETWORK_POLICIES:
        raise BuildContractError(
            f"unsupported network_policy: {spec.network_policy!r}"
        )
    if spec.timeout_seconds < 1:
        raise BuildContractError("timeout_seconds must be >= 1")
    if spec.disk_floor_bytes < 0 or spec.per_task_disk_bytes < 0:
        raise BuildContractError("disk limits must be non-negative")


def _enforce_and_verify_offline_guest(transport: GuestTransport) -> None:
    """Disconnect guest network adapters and verify dual offline state (host + guest).

    Enforces dual verification:
    1. Calls ``disconnect_network()`` if callable.
    2. Verifies host hypervisor reports all NICs disconnected via
       ``hypervisor_network_isolated() == True``.
    3. Verifies guest reports network is offline via
       ``network_online() == False``.

    Fail-closed: if either verification function is missing, returns an
    unfavorable status, or raises an exception, raises
    ``BuildPrerequisiteError("NETWORK_ISOLATION_NOT_ESTABLISHED: ...")``.
    """
    disconnect_fn = getattr(transport, "disconnect_network", None)
    hypervisor_fn = getattr(transport, "hypervisor_network_isolated", None)
    online_fn = getattr(transport, "network_online", None)

    if not callable(hypervisor_fn):
        raise BuildPrerequisiteError(
            "NETWORK_ISOLATION_NOT_ESTABLISHED: transport cannot verify host-side "
            "hypervisor_network_isolated() state"
        )
    if not callable(online_fn):
        raise BuildPrerequisiteError(
            "NETWORK_ISOLATION_NOT_ESTABLISHED: transport cannot verify guest network_online() state"
        )

    if callable(disconnect_fn):
        try:
            disconnect_fn()
        except Exception as exc:  # noqa: BLE001
            raise BuildPrerequisiteError(
                f"NETWORK_ISOLATION_NOT_ESTABLISHED: disconnect_network() failed: {exc}"
            ) from exc

    try:
        host_isolated = bool(hypervisor_fn())
    except Exception as exc:  # noqa: BLE001
        raise BuildPrerequisiteError(
            f"NETWORK_ISOLATION_NOT_ESTABLISHED: hypervisor_network_isolated() check failed: {exc}"
        ) from exc
    if not host_isolated:
        raise BuildPrerequisiteError(
            "NETWORK_ISOLATION_NOT_ESTABLISHED: host hypervisor reports virtual NIC still connected"
        )

    try:
        still_online = bool(online_fn())
    except Exception as exc:  # noqa: BLE001
        raise BuildPrerequisiteError(
            f"NETWORK_ISOLATION_NOT_ESTABLISHED: network_online() check failed: {exc}"
        ) from exc
    if still_online:
        raise BuildPrerequisiteError(
            "NETWORK_ISOLATION_NOT_ESTABLISHED: guest network remains online before untrusted build phase"
        )


def run_guest_build(
    spec: BuildSpec,
    transport: GuestTransport,
    *,
    clock=time.monotonic,
) -> GuestBuildResult:
    """Execute one build inside the guest, fail-closed at every step.

    The caller owns the transport.  This function never falls back to host
    execution and never treats a failed/partial build as compatible.

    Transport faults are **classified, not propagated**: a dead channel, a
    worker crash, or a guest timeout becomes ``INFRASTRUCTURE_FAILURE`` or
    ``TIMEOUT`` with a reason code, so a caller cannot mistake an infrastructure
    fault for an ordinary exception or for a task outcome.  A malformed build
    request is still a contract error and is raised.
    """
    _validate_spec(spec)
    started = clock()
    try:
        return _execute_guest_build(spec, transport, started=started, clock=clock)
    except BuildPrerequisiteError as exc:
        msg = str(exc)
        code = (
            "NETWORK_ISOLATION_NOT_ESTABLISHED"
            if "NETWORK_ISOLATION_NOT_ESTABLISHED" in msg
            else "PREREQUISITE_UNAVAILABLE"
        )
        return _failed(
            spec,
            status=STATUS_INFRASTRUCTURE_FAILURE,
            reason_code=code,
            started=started,
            stderr=msg,
            clock=clock,
        )
    except (TimeoutError, subprocess.TimeoutExpired) as exc:
        return _failed(
            spec,
            status=STATUS_TIMEOUT,
            reason_code="TRANSPORT_TIMEOUT",
            started=started,
            stderr=str(exc) or type(exc).__name__,
            clock=clock,
        )
    except BuildContractError:
        raise
    except Exception as exc:  # noqa: BLE001 - classify, never swallow silently
        return _failed(
            spec,
            status=STATUS_INFRASTRUCTURE_FAILURE,
            reason_code="TRANSPORT_FAILURE",
            started=started,
            stderr=f"{type(exc).__name__}: {exc}",
            clock=clock,
        )


def _execute_guest_build(
    spec: BuildSpec,
    transport: GuestTransport,
    *,
    started: float,
    clock=time.monotonic,
) -> GuestBuildResult:
    """The ordered guest sequence behind :func:`run_guest_build`.

    Implements the one-guest-per-task architecture with host-enforced
    acquisition-to-offline phase switch.
    """
    phase_timings: dict[str, float] = {}
    phase_details: dict[str, dict[str, float]] = {}
    last_mark = [started]

    def _mark(name: str) -> None:
        try:
            current = clock()
        except (StopIteration, RuntimeError):
            current = last_mark[0]
        duration = max(0.0, current - last_mark[0])
        phase_timings[name] = duration
        phase_details[name] = {
            "start": last_mark[0],
            "end": current,
            "duration": duration,
        }
        last_mark[0] = current

    parent = os.path.dirname(spec.workspace.rstrip("/\\"))

    # Phase 0: Preflight (disk space and Python interpreter version)
    required = spec.disk_floor_bytes + spec.per_task_disk_bytes
    free = int(transport.free_bytes(spec.workspace))
    if free < required:
        return _failed(
            spec,
            status=STATUS_INFRASTRUCTURE_FAILURE,
            reason_code="DISK_LOW",
            started=started,
            stderr=f"free={free} required={required}",
            phase_timings=phase_timings,
            phase_details=phase_details,
            clock=clock,
        )

    if spec.required_python:
        py_res = transport.run(
            ["python", "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"],
            cwd=parent,
            timeout=30,
        )
        _mark("python_preflight")
        if py_res.timed_out:
            return _failed(
                spec,
                status=STATUS_TIMEOUT,
                reason_code="PYTHON_CHECK_TIMEOUT",
                started=started,
                phase_timings=phase_timings,
                phase_details=phase_details,
                clock=clock,
            )
        if py_res.returncode != 0:
            return _failed(
                spec,
                status=STATUS_INFRASTRUCTURE_FAILURE,
                reason_code="PYTHON_UNAVAILABLE",
                started=started,
                stderr=py_res.stderr,
                phase_timings=phase_timings,
                phase_details=phase_details,
                clock=clock,
            )
        actual_py = py_res.stdout.strip()
        if not check_python_version_compatible(spec.required_python, actual_py):
            return _failed(
                spec,
                status=STATUS_INFRASTRUCTURE_FAILURE,
                reason_code="PYTHON_VERSION_MISMATCH",
                started=started,
                stderr=f"task requires Python {spec.required_python!r}, guest provides {actual_py!r}",
                phase_timings=phase_timings,
                phase_details=phase_details,
                clock=clock,
            )

    # Phase 1: Acquisition (Online phase)
    # Clone, checkout, verify revision, and run acquire_argv while network is attached.
    clone = transport.run(
        ["git", "clone", "--filter=blob:none", "--quiet", spec.repo_url, spec.workspace],
        cwd=parent,
        timeout=spec.timeout_seconds,
    )
    _mark("clone")
    if clone.timed_out:
        return _failed(spec, status=STATUS_TIMEOUT, reason_code="CLONE_TIMEOUT", started=started, stdout=clone.stdout, stderr=clone.stderr, phase_timings=phase_timings, phase_details=phase_details, clock=clock)
    if clone.returncode != 0:
        return _failed(spec, status=STATUS_INFRASTRUCTURE_FAILURE, reason_code="CLONE_FAILED", started=started, stdout=clone.stdout, stderr=clone.stderr, phase_timings=phase_timings, phase_details=phase_details, clock=clock)

    checkout = transport.run(
        ["git", "-C", spec.workspace, "checkout", "--quiet", spec.base_commit],
        cwd=parent,
        timeout=spec.timeout_seconds,
    )
    _mark("checkout")
    if checkout.timed_out:
        return _failed(spec, status=STATUS_TIMEOUT, reason_code="CHECKOUT_TIMEOUT", started=started, stdout=checkout.stdout, stderr=checkout.stderr, phase_timings=phase_timings, phase_details=phase_details, clock=clock)
    if checkout.returncode != 0:
        return _failed(spec, status=STATUS_INFRASTRUCTURE_FAILURE, reason_code="CHECKOUT_FAILED", started=started, stdout=checkout.stdout, stderr=checkout.stderr, phase_timings=phase_timings, phase_details=phase_details, clock=clock)

    revision = transport.run(
        ["git", "-C", spec.workspace, "rev-parse", "HEAD"],
        cwd=parent,
        timeout=spec.timeout_seconds,
    )
    _mark("rev_parse")
    if revision.returncode != 0 or revision.stdout.strip() != spec.base_commit:
        return _failed(
            spec,
            status=STATUS_INFRASTRUCTURE_FAILURE,
            reason_code="SOURCE_REVISION_MISMATCH",
            started=started,
            stdout=revision.stdout,
            stderr=revision.stderr,
            phase_timings=phase_timings,
            phase_details=phase_details,
            clock=clock,
        )

    if spec.acquire_argv:
        acquired = transport.run(
            spec.acquire_argv, cwd=spec.workspace, timeout=spec.timeout_seconds
        )
        _mark("acquire")
        if acquired.timed_out:
            return _failed(
                spec,
                status=STATUS_TIMEOUT,
                reason_code="ACQUIRE_TIMEOUT",
                started=started,
                stdout=acquired.stdout,
                stderr=acquired.stderr,
                phase_timings=phase_timings,
                phase_details=phase_details,
                clock=clock,
            )
        if acquired.returncode != 0:
            return _failed(
                spec,
                status=STATUS_BUILD_FAILURE,
                reason_code="ACQUIRE_FAILED",
                started=started,
                stdout=acquired.stdout,
                stderr=acquired.stderr,
                phase_timings=phase_timings,
                phase_details=phase_details,
                clock=clock,
            )

    # Phase 2: Host-enforced isolation transition
    must_isolate_for_build = bool(
        spec.require_offline_build_phase
        or spec.acquire_argv
        or spec.network_policy == "CONTROLLED_ACQUISITION_OFFLINE_EXECUTION"
    )
    network_isolated = False
    if must_isolate_for_build:
        _enforce_and_verify_offline_guest(transport)
        _mark("transition")
        network_isolated = True

    # Phase 3: Offline Execution (Patch apply, Install, Build, Test)
    # Untrusted code from candidate patch / build scripts runs ONLY after transition to offline.
    actual_patch = transport.sha256(spec.test_patch)
    _mark("patch_verify")
    if actual_patch != spec.test_patch_sha256:
        return _failed(
            spec,
            status=STATUS_INFRASTRUCTURE_FAILURE,
            reason_code="PATCH_DIGEST_MISMATCH",
            started=started,
            stderr=f"expected={spec.test_patch_sha256} actual={actual_patch}",
            phase_timings=phase_timings,
            phase_details=phase_details,
            clock=clock,
        )

    applied = transport.run(
        ["git", "-C", spec.workspace, "apply", spec.test_patch],
        cwd=parent,
        timeout=spec.timeout_seconds,
    )
    _mark("patch_apply")
    if applied.timed_out:
        return _failed(spec, status=STATUS_TIMEOUT, reason_code="PATCH_APPLY_TIMEOUT", started=started, stdout=applied.stdout, stderr=applied.stderr, phase_timings=phase_timings, phase_details=phase_details, clock=clock)
    if applied.returncode != 0:
        return _failed(spec, status=STATUS_INFRASTRUCTURE_FAILURE, reason_code="PATCH_APPLY_FAILED", started=started, stdout=applied.stdout, stderr=applied.stderr, phase_timings=phase_timings, phase_details=phase_details, clock=clock)

    if network_isolated:
        _enforce_and_verify_offline_guest(transport)

    installed = transport.run(
        spec.install_argv, cwd=spec.workspace, timeout=spec.timeout_seconds
    )
    _mark("install")
    if installed.timed_out:
        return _failed(spec, status=STATUS_TIMEOUT, reason_code="INSTALL_TIMEOUT", started=started, stdout=installed.stdout, stderr=installed.stderr, phase_timings=phase_timings, phase_details=phase_details, clock=clock)
    if installed.returncode != 0:
        return _failed(spec, status=STATUS_BUILD_FAILURE, reason_code="INSTALL_FAILED", started=started, stdout=installed.stdout, stderr=installed.stderr, phase_timings=phase_timings, phase_details=phase_details, clock=clock)

    if network_isolated:
        _enforce_and_verify_offline_guest(transport)

    built = transport.run(
        spec.build_argv, cwd=spec.workspace, timeout=spec.timeout_seconds
    )
    _mark("build")
    if built.timed_out:
        return _failed(spec, status=STATUS_TIMEOUT, reason_code="BUILD_TIMEOUT", started=started, stdout=built.stdout, stderr=built.stderr, phase_timings=phase_timings, phase_details=phase_details, clock=clock)
    if built.returncode != 0:
        return _failed(spec, status=STATUS_BUILD_FAILURE, reason_code="BUILD_FAILED", started=started, stdout=built.stdout, stderr=built.stderr, phase_timings=phase_timings, phase_details=phase_details, clock=clock)

    if spec.test_argv:
        if network_isolated:
            _enforce_and_verify_offline_guest(transport)
        tested = transport.run(
            spec.test_argv, cwd=spec.workspace, timeout=spec.timeout_seconds
        )
        _mark("test")
        if tested.timed_out:
            return _failed(spec, status=STATUS_TIMEOUT, reason_code="TEST_TIMEOUT", started=started, stdout=tested.stdout, stderr=tested.stderr, phase_timings=phase_timings, phase_details=phase_details, clock=clock)
        if tested.returncode != 0:
            return _failed(spec, status=STATUS_BUILD_FAILURE, reason_code="TEST_FAILED", started=started, stdout=tested.stdout, stderr=tested.stderr, phase_timings=phase_timings, phase_details=phase_details, clock=clock)

    try:
        final_now = clock()
    except (StopIteration, RuntimeError):
        final_now = last_mark[0]

    return GuestBuildResult(
        task_id=spec.task_id,
        status=STATUS_BUILT,
        classification=CLASS_COMPATIBLE,
        workspace=spec.workspace,
        reason_code="",
        stdout_tail=_tail(built.stdout),
        stderr_tail=_tail(built.stderr),
        duration_seconds=max(0.0, final_now - started),
        failure="",
        network_isolated_for_build=network_isolated,
        phase_timings=phase_timings,
        phase_details=phase_details,
    )


def precheck_collection(
    *,
    task_id: str,
    task_json: pathlib.Path | str,
    test_patch: pathlib.Path | str,
    test_patch_sha256: str,
    repo_url: str,
    base_commit: str,
) -> list[str]:
    """Host-side checks that must pass before any guest transfer/build.

    Returns a list of human-readable failures; an empty list means the inputs
    are internally consistent.  It performs no network or guest operation.
    """
    errors: list[str] = []
    if not TASK_ID_RE.fullmatch(task_id):
        errors.append(f"invalid task id: {task_id!r}")
    if not GITHUB_REPO_RE.fullmatch(repo_url):
        errors.append(f"repository URL is not an approved GitHub URL: {repo_url!r}")
    if not COMMIT_RE.fullmatch(base_commit):
        errors.append(f"base_commit is not a 40-hex commit: {base_commit!r}")
    task_path = pathlib.Path(task_json)
    patch_path = pathlib.Path(test_patch)
    if not task_path.is_file():
        errors.append(f"task record missing: {task_path}")
    else:
        import json

        try:
            record = json.loads(task_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"task record unreadable: {exc}")
            record = {}
        if not isinstance(record, dict):
            errors.append("task record is not an object")
        else:
            match = re.fullmatch(
                r"https://github\.com/([A-Za-z0-9_.\-]+)/([A-Za-z0-9_.\-]+)\.git",
                repo_url,
            )
            expected_repo = f"{match.group(1)}/{match.group(2)}" if match else ""
            recorded_repo = str(record.get("repo") or "")
            if recorded_repo and expected_repo and recorded_repo != expected_repo:
                errors.append("task record repo does not match repo_url")
            if str(record.get("base_commit") or "") not in ("", base_commit):
                errors.append("task record base_commit does not match")
            if "test_patch" in record and isinstance(record["test_patch"], str):
                embedded_sha = hashlib.sha256(record["test_patch"].encode("utf-8")).hexdigest()
                if embedded_sha != test_patch_sha256:
                    errors.append("embedded task record test_patch does not match test_patch_sha256")
    if not patch_path.is_file():
        errors.append(f"test patch missing: {patch_path}")
    elif not SHA256_RE.fullmatch(test_patch_sha256):
        errors.append("test_patch_sha256 must be 64 lowercase hex")
    else:
        digest = hashlib.sha256(patch_path.read_bytes()).hexdigest()
        if digest != test_patch_sha256:
            errors.append("test patch digest mismatch")
    return errors


@dataclass(frozen=True)
class ArtifactEntry:
    relative_path: str
    size_bytes: int
    sha256: str


def _is_reparse(path: pathlib.Path) -> bool:
    try:
        if path.is_symlink():
            return True
        st = os.lstat(path)
        return bool(
            getattr(st, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        )
    except FileNotFoundError:
        return False


def _safe_relative(relative_path: str) -> pathlib.PurePosixPath:
    text = str(relative_path).replace("\\", "/")
    pure = pathlib.PurePosixPath(text)
    if not text or pure.is_absolute() or ".." in pure.parts or ":" in text:
        raise BuildContractError(f"unsafe artifact path: {relative_path!r}")
    return pure


def validate_artifact_tree(
    root: pathlib.Path | str,
    artifacts: Iterable[ArtifactEntry],
    *,
    max_files: int = 50_000,
    max_bytes: int = 2_000_000_000,
    allowed_suffixes: Sequence[str] = (),
) -> tuple[ArtifactEntry, ...]:
    """Validate a returned artifact set before it is used on the host.

    Rejects traversal/absolute/drive paths, symlinks/reparse points, duplicate
    paths, unexpected suffixes, excessive count/size, and digest mismatches.
    """
    root_path = pathlib.Path(root).resolve(strict=False)
    entries = tuple(artifacts)
    if len(entries) > max_files:
        raise BuildContractError(f"artifact count {len(entries)} exceeds {max_files}")
    total = 0
    seen: set[str] = set()
    seen_paths: list[pathlib.Path] = []
    out: list[ArtifactEntry] = []
    for entry in entries:
        pure = _safe_relative(entry.relative_path)
        key = pure.as_posix()
        collision_key = key.casefold() if os.name == "nt" else key
        if collision_key in seen:
            raise BuildContractError(f"duplicate artifact path: {entry.relative_path!r}")
        seen.add(collision_key)
        if allowed_suffixes and not key.lower().endswith(tuple(s.lower() for s in allowed_suffixes)):
            raise BuildContractError(f"artifact suffix not permitted: {entry.relative_path!r}")
        path = root_path
        for part in pure.parts:
            path = path / part
            if _is_reparse(path):
                raise BuildContractError(f"reparse point in artifact path: {entry.relative_path!r}")
        resolved = path.resolve(strict=False)
        if resolved != root_path and root_path not in resolved.parents:
            raise BuildContractError(f"artifact escapes root: {entry.relative_path!r}")
        if not resolved.is_file():
            raise BuildContractError(f"artifact missing: {entry.relative_path!r}")
        for existing in seen_paths:
            try:
                if os.path.samefile(resolved, existing):
                    raise BuildContractError(
                        f"duplicate artifact alias (same physical file): "
                        f"{entry.relative_path!r}"
                    )
            except OSError:
                continue
        seen_paths.append(resolved)
        size = resolved.stat().st_size
        total += size
        if total > max_bytes:
            raise BuildContractError(f"artifact bytes exceed {max_bytes}")
        if size != entry.size_bytes:
            raise BuildContractError(f"artifact size mismatch: {entry.relative_path!r}")
        if hashlib.sha256(resolved.read_bytes()).hexdigest() != entry.sha256:
            raise BuildContractError(f"artifact digest mismatch: {entry.relative_path!r}")
        out.append(ArtifactEntry(key, size, entry.sha256))
    return tuple(out)


def publish_artifacts_atomically(staging: pathlib.Path | str, final: pathlib.Path | str) -> None:
    """Publish a validated staging directory to a new final path, atomically.

    Atomic replacement requires staging and final to share a filesystem.  A
    cross-volume move is refused rather than silently degraded into a
    non-atomic copy.  The final parent is created explicitly.
    """
    staging_path = pathlib.Path(staging)
    final_path = pathlib.Path(final)
    if not staging_path.is_dir():
        raise BuildContractError(f"staging directory missing: {staging_path}")
    if final_path.exists():
        raise BuildContractError(f"final artifact path already exists: {final_path}")
    if os.name == "nt" and staging_path.drive.lower() != final_path.drive.lower():
        raise BuildContractError(
            f"cross-volume publication is not atomic: {staging_path.drive} -> {final_path.drive}"
        )
    final_path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            if staging_path.stat().st_dev != final_path.parent.stat().st_dev:
                raise BuildContractError(
                    "cross-filesystem publication is not atomic"
                )
        except OSError as exc:
            raise BuildContractError(f"cannot compare filesystems: {exc}") from exc
    try:
        os.replace(str(staging_path), str(final_path))
    except OSError as exc:
        raise BuildContractError(f"atomic publication failed: {exc}") from exc




#: Protected execution VMs that must never be used or altered as a build VM.
PROTECTED_EXECUTION_VMS: frozenset[str] = frozenset(
    {"F2-S8-VM1", "F2-Isolation-VM"}
)

#: Approved uv 0.9.5 binary SHA-256 digest (Trust §4.1 / bootstrap contract).
UV_APPROVED_SHA256 = (
    "92fcafc6f7512ec3a0fb42bf6bf84730737252c6d3d7cae4f6f97678d746da3a"
)

#: Supported build-time network policies.
ALLOWED_BUILD_NETWORK_POLICIES: frozenset[str] = frozenset(
    {
        "OFFLINE_WHEELHOUSE_ONLY",
        "PYPI_INSTALL_ONLY_COLLECTION_OFFLINE",
        "CONTROLLED_ACQUISITION_OFFLINE_EXECUTION",
    }
)


def _ps_quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _powershell_command(argv: Sequence[str], cwd: str) -> str:
    executable = _ps_quote(argv[0])
    arguments = " ".join(_ps_quote(a) for a in argv[1:])
    command = f"& {executable}" + (f" {arguments}" if arguments else "")
    return f"Set-Location -LiteralPath {_ps_quote(cwd)}; {command}"


class ChannelBuildTransport:
    """Concrete ``GuestTransport`` over an authenticated guest channel.

    The channel is the existing PowerShell Direct / VMBus object (for example
    ``qwen_train.f2_s8_pilot_live.PsDirectChannel``).  This wrapper constructs
    PowerShell commands and parses their output; it never reads the credential
    file itself and never runs on the host.
    """

    def __init__(
        self,
        channel: object,
        *,
        host_disconnect_fn: object | None = None,
        hypervisor_isolation_fn: object | None = None,
        vm_name: str = "",
        pwsh: str = "powershell",
    ) -> None:
        self.channel = channel
        self.host_disconnect_fn = host_disconnect_fn
        self.hypervisor_isolation_fn = hypervisor_isolation_fn
        self.vm_name = vm_name.strip()
        self.pwsh = pwsh
        if self.vm_name and self.vm_name.lower() in {v.lower() for v in PROTECTED_EXECUTION_VMS}:
            raise BuildPrerequisiteError(
                f"refusing to bind transport to protected execution VM {self.vm_name!r}"
            )

    @classmethod
    def from_powershell_direct(
        cls,
        *,
        vm_name: str,
        credential_path: str,
        pwsh: str = "pwsh",
        host_disconnect_fn: object | None = None,
        hypervisor_isolation_fn: object | None = None,
    ) -> "ChannelBuildTransport":
        from qwen_train.f2_s8_pilot_live import PsDirectChannel

        return cls(
            PsDirectChannel(
                vm_name=vm_name, credential_path=credential_path, pwsh=pwsh
            ),
            host_disconnect_fn=host_disconnect_fn,
            hypervisor_isolation_fn=hypervisor_isolation_fn,
            vm_name=vm_name,
            pwsh=pwsh,
        )

    def run(
        self, argv: Sequence[str], *, cwd: str, timeout: int
    ) -> GuestCommandResult:
        command = _powershell_command(argv, cwd)
        try:
            rc, out, err = self.channel.run(command, timeout=timeout)  # type: ignore[attr-defined]
        except subprocess.TimeoutExpired as exc:
            return GuestCommandResult(
                returncode=-1,
                stderr=f"guest command timed out after {timeout}s: {exc}",
                timed_out=True,
            )
        return GuestCommandResult(int(rc), str(out), str(err))

    def sha256(self, path: str) -> str:
        command = (
            f"(Get-FileHash -Algorithm SHA256 -LiteralPath {_ps_quote(path)})"
            ".Hash.ToLower()"
        )
        rc, out, err = self.channel.run(command, timeout=60)  # type: ignore[attr-defined]
        digest = str(out).strip().lower()
        if int(rc) != 0 or not SHA256_RE.fullmatch(digest):
            raise BuildPrerequisiteError(
                f"cannot hash guest patch {path!r}: rc={rc} stderr={str(err)[-300:]}"
            )
        return digest

    def free_bytes(self, path: str) -> int:
        drive = pathlib.PureWindowsPath(path).drive or "C:"
        letter = drive[0].upper()
        command = f"(Get-PSDrive -Name {_ps_quote(letter)}).Free"
        rc, out, err = self.channel.run(command, timeout=60)  # type: ignore[attr-defined]
        text = str(out).strip()
        if int(rc) != 0 or not text.isdigit():
            raise BuildPrerequisiteError(
                f"cannot read guest free space for {path!r}: rc={rc} stderr={str(err)[-300:]}"
            )
        return int(text)

    def disconnect_network(self) -> None:
        if callable(self.host_disconnect_fn):
            self.host_disconnect_fn()
        elif self.vm_name:
            if self.vm_name.lower() in {v.lower() for v in PROTECTED_EXECUTION_VMS}:
                raise BuildPrerequisiteError(
                    f"refusing to disconnect network on protected VM {self.vm_name!r}"
                )
            ps_cmd = (
                f"Get-VMNetworkAdapter -VMName '{self.vm_name}' -ErrorAction SilentlyContinue | "
                "Disconnect-VMNetworkAdapter -ErrorAction Stop"
            )
            res = subprocess.run(
                [self.pwsh, "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if res.returncode != 0:
                raise BuildPrerequisiteError(
                    f"failed to disconnect Hyper-V network adapter on {self.vm_name!r}: {res.stderr}"
                )

    def hypervisor_network_isolated(self) -> bool:
        """Query host hypervisor to confirm zero connected switches for this VM."""
        if callable(self.hypervisor_isolation_fn):
            return bool(self.hypervisor_isolation_fn())
        if not self.vm_name:
            return False
        if self.vm_name.lower() in {v.lower() for v in PROTECTED_EXECUTION_VMS}:
            raise BuildPrerequisiteError(
                f"refusing to query protected VM {self.vm_name!r}"
            )
        ps_cmd = (
            f"@((Get-VMNetworkAdapter -VMName '{self.vm_name}' -ErrorAction SilentlyContinue | "
            "Where-Object { $_.SwitchName }).Count)"
        )
        try:
            res = subprocess.run(
                [self.pwsh, "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except Exception as exc:  # noqa: BLE001
            raise BuildPrerequisiteError(
                f"hypervisor isolation check failed on {self.vm_name!r}: {exc}"
            ) from exc
        if res.returncode != 0:
            raise BuildPrerequisiteError(
                f"cannot query Hyper-V network adapter on {self.vm_name!r}: {res.stderr.strip()}"
            )
        out = res.stdout.strip()
        if not out.isdigit():
            raise BuildPrerequisiteError(
                f"unexpected hypervisor query output on {self.vm_name!r}: {out!r}"
            )
        return int(out) == 0

    def network_online(self) -> bool:
        """Probe inside the guest whether any non-loopback IP adapter/route is up.

        Fail-closed: a non-zero exit code or unparseable output raises
        ``BuildPrerequisiteError`` rather than assuming the guest is offline.
        """
        command = (
            "$up = @(Get-NetAdapter -ErrorAction SilentlyContinue | "
            "Where-Object { $_.Status -eq 'Up' }); "
            "$routes = @(Get-NetRoute -DestinationPrefix '0.0.0.0/0' "
            "-ErrorAction SilentlyContinue); "
            "if ($up.Count -gt 0 -or $routes.Count -gt 0) { 'ONLINE' } else { 'OFFLINE' }"
        )
        rc, out, err = self.channel.run(command, timeout=60)  # type: ignore[attr-defined]
        token = str(out).strip().upper()
        if int(rc) != 0 or token not in ("ONLINE", "OFFLINE"):
            raise BuildPrerequisiteError(
                f"cannot verify guest network state: rc={rc} out={token!r} stderr={str(err)[-300:]}"
            )
        return token == "ONLINE"



GUEST_READINESS_SCHEMA = "f2-s8-build-guest-readiness/v1"


@dataclass(frozen=True)
class BuildEnvironmentPreflight:
    vm_name: str
    media_sha256: str
    guest_user: str
    uv_sha256: str
    guest_free_bytes: int
    network_policy: str
    network_isolation_status: str = "NOT_ESTABLISHED"
    python_version: str = ""


def preflight_build_environment(
    *,
    vm_name: str,
    network_policy: str,
    readiness_report: dict[str, object] | pathlib.Path | str,
    media_path: pathlib.Path | str | None = None,
    expected_media_sha256: str | None = None,
    expected_uv_sha256: str = UV_APPROVED_SHA256,
    disk_floor_bytes: int = 25_000_000_000,
    per_task_disk_bytes: int = 3_500_000_000,
    forbidden_switches: Iterable[str] = ("F2-Internal-Switch",),
    configured_switch: str = "",
    phase_disconnect_verified: bool = False,
    required_python_version: str | None = None,
) -> BuildEnvironmentPreflight:
    """Safe preflight check for build-VM identity, media integrity, storage,
    toolchain, and network configuration before any build probe begins.

    Raises ``BuildPrerequisiteError`` on any safety or readiness failure.
    """
    import json

    clean_vm = str(vm_name or "").strip()
    if not clean_vm:
        raise BuildPrerequisiteError("build VM name is required")
    if clean_vm.lower() in {v.lower() for v in PROTECTED_EXECUTION_VMS}:
        raise BuildPrerequisiteError(
            f"refusing to use protected execution VM {clean_vm!r} as build VM"
        )

    if network_policy not in ALLOWED_BUILD_NETWORK_POLICIES:
        raise BuildPrerequisiteError(
            f"unsupported build network_policy: {network_policy!r}"
        )

    forbidden_sw = {s.lower() for s in forbidden_switches if s}
    clean_sw = configured_switch.strip()
    if clean_sw and clean_sw.lower() in forbidden_sw:
        raise BuildPrerequisiteError(
            f"build VM switch {configured_switch!r} is forbidden (protected internal switch)"
        )
    if (
        network_policy == "OFFLINE_WHEELHOUSE_ONLY"
        and clean_sw
        and clean_sw.lower() not in ("", "none")
    ):
        raise BuildPrerequisiteError(
            f"OFFLINE_WHEELHOUSE_ONLY forbids external switch {configured_switch!r}"
        )
    if (
        network_policy == "CONTROLLED_ACQUISITION_OFFLINE_EXECUTION"
        and clean_sw
        and clean_sw.lower() not in ("", "none")
        and not phase_disconnect_verified
    ):
        raise BuildPrerequisiteError(
            "NETWORK_ISOLATION_NOT_ESTABLISHED: CONTROLLED_ACQUISITION_OFFLINE_EXECUTION "
            f"with switch {configured_switch!r} requires phase_disconnect_verified=True "
            "(Hyper-V Default Switch alone does not enforce destination allowlists or "
            "block candidate build-backend egress)"
        )
    isolation_status = (
        "ENFORCED_OFFLINE"
        if clean_sw.lower() in ("", "none") or phase_disconnect_verified
        else "CONFIGURED_ONLY"
    )

    media_sha = ""
    if media_path is not None or expected_media_sha256 is not None:
        if media_path is None:
            raise BuildPrerequisiteError(
                "media_path is required when expected_media_sha256 is specified"
            )
        mp = pathlib.Path(media_path)
        if not mp.is_file():
            raise BuildPrerequisiteError(f"build VM media missing: {mp}")
        if mp.suffix.lower() not in (".vhdx", ".vhd", ".iso"):
            raise BuildPrerequisiteError(
                f"unsupported build VM media extension: {mp.suffix!r}"
            )
        h = hashlib.sha256()
        with open(mp, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        media_sha = h.hexdigest()
        if expected_media_sha256 is not None:
            if not SHA256_RE.fullmatch(expected_media_sha256.lower()):
                raise BuildPrerequisiteError(
                    "expected_media_sha256 must be 64 hex characters"
                )
            if media_sha != expected_media_sha256.lower():
                raise BuildPrerequisiteError(
                    f"build VM media SHA-256 mismatch: actual={media_sha} "
                    f"expected={expected_media_sha256.lower()}"
                )

    if isinstance(readiness_report, (str, pathlib.Path)):
        rp = pathlib.Path(readiness_report)
        if not rp.is_file():
            raise BuildPrerequisiteError(f"guest readiness report missing: {rp}")
        try:
            report_data = json.loads(rp.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise BuildPrerequisiteError(
                f"guest readiness report unreadable: {exc}"
            ) from exc
    elif isinstance(readiness_report, dict):
        report_data = dict(readiness_report)
    else:
        raise BuildPrerequisiteError("readiness_report must be a dict or path")

    if report_data.get("schema") != GUEST_READINESS_SCHEMA:
        raise BuildPrerequisiteError(
            f"unsupported guest readiness schema: {report_data.get('schema')!r}"
        )

    guest_host = str(report_data.get("computer_name") or "").strip()
    if guest_host and guest_host.lower() in {
        v.lower() for v in PROTECTED_EXECUTION_VMS
    }:
        raise BuildPrerequisiteError(
            f"guest computer_name {guest_host!r} matches a protected execution VM"
        )

    guest_user = str(report_data.get("user") or "").strip()
    if not guest_user:
        raise BuildPrerequisiteError("guest readiness report missing 'user'")
    if report_data.get("is_admin") is True:
        raise BuildPrerequisiteError(
            f"guest builder account {guest_user!r} must be unprivileged (is_admin=True)"
        )

    for tool_key in ("python", "git", "uv"):
        if not str(report_data.get(tool_key) or "").strip():
            raise BuildPrerequisiteError(
                f"guest readiness report missing required toolchain entry: {tool_key!r}"
            )

    uv_sha = str(report_data.get("uv_sha256") or "").strip().lower()
    if not SHA256_RE.fullmatch(uv_sha):
        raise BuildPrerequisiteError(
            "guest readiness report missing 64-hex uv_sha256"
        )
    if expected_uv_sha256 and uv_sha != expected_uv_sha256.lower():
        raise BuildPrerequisiteError(
            f"guest uv_sha256 mismatch: actual={uv_sha} expected={expected_uv_sha256.lower()}"
        )

    raw_free = report_data.get("free_bytes")
    if isinstance(raw_free, bool) or not isinstance(raw_free, int) or raw_free < 0:
        raise BuildPrerequisiteError(
            "guest readiness report missing valid integer free_bytes"
        )
    required_bytes = max(0, disk_floor_bytes) + max(0, per_task_disk_bytes)
    if raw_free < required_bytes:
        raise BuildPrerequisiteError(
            f"guest free disk space {raw_free} is below required {required_bytes} bytes"
        )

    guest_py_ver = str(report_data.get("python_version") or report_data.get("python") or "").strip()
    if required_python_version and guest_py_ver:
        if not check_python_version_compatible(required_python_version, guest_py_ver):
            raise BuildPrerequisiteError(
                f"guest Python version {guest_py_ver!r} does not satisfy {required_python_version!r}"
            )

    return BuildEnvironmentPreflight(
        vm_name=clean_vm,
        media_sha256=media_sha,
        guest_user=guest_user,
        uv_sha256=uv_sha,
        guest_free_bytes=raw_free,
        network_policy=network_policy,
        network_isolation_status=isolation_status,
        python_version=guest_py_ver,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for S8 VM-contained guest build operations."""
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser(description="VM-contained S8 guest build runner")
    ap.add_argument("--task-id", default="openai__openai-agents-python-1619", help="Target task ID")
    ap.add_argument("--grant-file", default=os.environ.get("F2_AUTHORIZATION_FILE"), help="Path to signed S8 authorization grant")
    ap.add_argument("--verify-grant-only", action="store_true", help="Perform non-reserving authorization verification and exit")
    ap.add_argument("--vm-name", default="F2-S8-BUILD1", help="Target Hyper-V build VM name")
    ap.add_argument("--credential-path", default=os.environ.get("F2_GUEST_CREDENTIAL_PATH", r"f2builder.cred"), help="PowerShell Direct credential XML path")
    ap.add_argument("--network-policy", default="CONTROLLED_ACQUISITION_OFFLINE_EXECUTION")
    ap.add_argument("--authorized-acquisition-policy", default="", help="Explicitly authorized acquisition policy")
    ap.add_argument("--outbound-switch", default="Default Switch", help="Hyper-V switch for Phase 1 acquisition")
    ap.add_argument("--readiness-report", default=os.environ.get("F2_GUEST_READINESS_REPORT", r"guest_readiness.json"))
    ap.add_argument("--inputs-dir", default=os.environ.get("F2_INPUTS_DIR", r"data\f2_population\inputs"))
    ap.add_argument("--patches-dir", default=os.environ.get("F2_PATCHES_DIR", r"data\f2_population\patches"))
    ap.add_argument("--workspace-root", default=os.environ.get("F2_WORKSPACE_ROOT", r"C:\S8Build\work"))
    ap.add_argument("--staging-dir", default=os.environ.get("F2_STAGING_DIR", r"data\staging"))
    ap.add_argument("--output-dir", default=os.environ.get("F2_PROBE_OUTPUT_DIR", r"data\probe_output"))
    ap.add_argument("--registration-file", default=os.environ.get("F2_REGISTRATION_FILE", r"data\f2_population\registration\registered_tasks.json"))
    ap.add_argument("--registration-manifest", default=os.environ.get("F2_REGISTRATION_MANIFEST", r"data\f2_population\registration\registered_tasks.provenance.json"))
    ap.add_argument("--compat-report", default=os.environ.get("F2_COMPAT_REPORT", r"data\f2_population\plugin_compat\plugin_compat_report.json"))
    ap.add_argument("--exclusion-policy", default=os.environ.get("F2_EXCLUSION_POLICY", ""))
    ap.add_argument("--ledgers", nargs="*", default=None)
    ap.add_argument("--dry-run", action="store_true", help="Run synthetic simulation without live VM")
    ap.add_argument("--live", action="store_true", help="Run live build inside guest VM")

    args = ap.parse_args(argv)

    ledger_list: list[str] = []
    if args.ledgers is not None:
        ledger_list = list(args.ledgers)
    elif os.environ.get("F2_LEDGERS"):
        ledger_list = [p.strip() for p in os.environ["F2_LEDGERS"].split(";") if p.strip()]

    if not args.grant_file or not pathlib.Path(args.grant_file).is_file():
        print(f"GRANT_NOT_FOUND: grant file missing or unreadable: {args.grant_file!r}", file=sys.stderr)
        return 2

    from qwen_train.f2_s8_v2_gate import authorize_s8_probe_run

    try:
        authorized = authorize_s8_probe_run(
            grant_path=args.grant_file,
            registration_file=args.registration_file,
            registration_manifest_file=args.registration_manifest,
            compat_report_file=args.compat_report,
            exclusion_policy_file=args.exclusion_policy,
            ledger_paths=ledger_list,
            reserve_execution=False,
        )
    except Exception as exc:
        print(f"PROBE_NOT_AUTHORIZED: {exc}", file=sys.stderr)
        return 2

    if args.task_id not in authorized.selected_task_ids:
        print(
            f"TASK_NOT_AUTHORIZED: task {args.task_id!r} is not in grant's selected candidates: "
            f"{list(authorized.selected_task_ids)[:3]}",
            file=sys.stderr,
        )
        return 2

    if args.verify_grant_only:
        print(
            json.dumps(
                {
                    "status": "GRANT_VERIFIED",
                    "task_id": args.task_id,
                    "pool_sha": authorized.probe_pool_digest,
                    "selection_sha": authorized.probe_selection_digest,
                    "selected_tasks": list(authorized.selected_task_ids),
                    "network_policy": authorized.network_policy,
                    "max_total_tasks": authorized.budget_limits.get("max_total_tasks"),
                    "batch_limit": authorized.batch_limit,
                    "target_compatible": authorized.target_compatible,
                },
                indent=2,
            )
        )
        return 0

    if not args.dry_run and not args.live:
        print("Specify either --dry-run or --live to execute build.", file=sys.stderr)
        return 2

    clean_vm = args.vm_name.strip()
    if clean_vm.lower() in {v.lower() for v in PROTECTED_EXECUTION_VMS}:
        print(f"SECURITY_REFUSAL: refusing to use protected VM {clean_vm!r}", file=sys.stderr)
        return 2

    if args.network_policy == "CONTROLLED_ACQUISITION_OFFLINE_EXECUTION":
        if args.authorized_acquisition_policy != "CONTROLLED_ACQUISITION_OFFLINE_EXECUTION":
            print(
                "POLICY_REFUSAL: CONTROLLED_ACQUISITION_OFFLINE_EXECUTION requires explicit "
                "--authorized-acquisition-policy CONTROLLED_ACQUISITION_OFFLINE_EXECUTION flag",
                file=sys.stderr,
            )
            return 2

    inputs_dir = pathlib.Path(args.inputs_dir)
    patches_dir = pathlib.Path(args.patches_dir)
    task_json_path = inputs_dir / f"{args.task_id}.json"
    test_patch_path = patches_dir / f"{args.task_id}.test_patch"

    if not task_json_path.is_file():
        print(f"INPUT_MISSING: task input JSON missing: {task_json_path}", file=sys.stderr)
        return 2
    if not test_patch_path.is_file():
        print(f"INPUT_MISSING: test patch missing: {test_patch_path}", file=sys.stderr)
        return 2

    task_data = json.loads(task_json_path.read_text(encoding="utf-8"))
    repo_url = f"https://github.com/{task_data['repo']}.git" if not task_data.get("repo_url") else task_data["repo_url"]
    base_commit = str(task_data.get("base_commit") or "").strip()
    patch_bytes = test_patch_path.read_bytes()
    patch_sha = hashlib.sha256(patch_bytes).hexdigest()

    precheck_errs = precheck_collection(
        task_id=args.task_id,
        task_json=task_json_path,
        test_patch=test_patch_path,
        test_patch_sha256=patch_sha,
        repo_url=repo_url,
        base_commit=base_commit,
    )
    if precheck_errs:
        print(f"PRECHECK_FAILED: {precheck_errs}", file=sys.stderr)
        return 2

    spec = BuildSpec(
        task_id=args.task_id,
        repo_url=repo_url,
        base_commit=base_commit,
        workspace=f"{args.workspace_root}\\{args.task_id}",
        test_patch=f"C:\\S8Build\\in\\{args.task_id}.test_patch",
        test_patch_sha256=patch_sha,
        install_argv=tuple(task_data.get("install_argv") or ("python", "-m", "pip", "install", "-e", ".")),
        build_argv=tuple(task_data.get("build_argv") or ("python", "-m", "build")),
        network_policy=args.network_policy,
        require_offline_build_phase=True,
    )

    if args.dry_run:
        print(f"[DRY RUN] Spec validated: task_id={spec.task_id} repo={spec.repo_url} commit={spec.base_commit}")
        return 0

    transport = ChannelBuildTransport.from_powershell_direct(
        vm_name=args.vm_name,
        credential_path=args.credential_path,
    )

    build_res = run_guest_build(spec, transport)
    print(
        json.dumps(
            {
                "task_id": build_res.task_id,
                "status": build_res.status,
                "classification": build_res.classification,
                "reason_code": build_res.reason_code,
                "duration_seconds": build_res.duration_seconds,
                "network_isolated_for_build": build_res.network_isolated_for_build,
            },
            indent=2,
        )
    )
    return 0 if build_res.status == STATUS_BUILT else 1


if __name__ == "__main__":
    raise SystemExit(main())
