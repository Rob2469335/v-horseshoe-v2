"""Guest-build contract tests with fake transports and temporary directories."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess

import pytest

from qwen_train.f2_s8_build_guest import (
    CLASS_BUILD_FAILURE,
    CLASS_COMPATIBLE,
    GUEST_READINESS_SCHEMA,
    STATUS_BUILD_FAILURE,
    STATUS_BUILT,
    STATUS_INFRASTRUCTURE_FAILURE,
    STATUS_TIMEOUT,
    UV_APPROVED_SHA256,
    ArtifactEntry,
    BuildContractError,
    BuildPrerequisiteError,
    BuildSpec,
    ChannelBuildTransport,
    GuestCommandResult,
    precheck_collection,
    preflight_build_environment,
    publish_artifacts_atomically,
    run_guest_build,
    validate_artifact_tree,
    main,
)

TASK = "acme__widget-1"
COMMIT = "a" * 40
PATCH = "C:/stage/widget.test_patch"
WS = "C:/build/acme__widget-1"
PATCH_SHA = "b" * 64


def _spec(**over):
    base = dict(
        task_id=TASK,
        repo_url="https://github.com/acme/widget.git",
        base_commit=COMMIT,
        workspace=WS,
        test_patch=PATCH,
        test_patch_sha256=PATCH_SHA,
        install_argv=("python", "-m", "pip", "install", "-e", "."),
        build_argv=("python", "-m", "build"),
        timeout_seconds=60,
        disk_floor_bytes=100,
        per_task_disk_bytes=50,
    )
    base.update(over)
    return BuildSpec(**base)


class FakeTransport:
    def __init__(
        self,
        *,
        free=10**9,
        handler=None,
        hashes=None,
        online=False,
        disconnect_clears_online=True,
        hypervisor_isolated=None,
        disconnect_sets_hypervisor_isolated=True,
        disconnect_callback=None,
    ):
        self.free = free
        self.handler = handler
        self.hashes = hashes or {PATCH: PATCH_SHA}
        self.calls = []
        self.online = online
        self.disconnect_clears_online = disconnect_clears_online
        if hypervisor_isolated is None:
            self.hypervisor_isolated = not online
        else:
            self.hypervisor_isolated = hypervisor_isolated
        self.disconnect_sets_hypervisor_isolated = disconnect_sets_hypervisor_isolated
        self.disconnect_callback = disconnect_callback
        self.disconnect_calls = 0
        self.online_checks = 0
        self.hypervisor_checks = 0

    def run(self, argv, *, cwd, timeout):
        self.calls.append((list(argv), cwd, timeout))
        if self.handler is not None:
            return self.handler(list(argv), cwd, timeout)
        return GuestCommandResult(0, "", "")

    def sha256(self, path):
        return self.hashes.get(path, "0" * 64)

    def free_bytes(self, path):
        return self.free

    def disconnect_network(self):
        self.disconnect_calls += 1
        if callable(self.disconnect_callback):
            self.disconnect_callback()
        if self.disconnect_clears_online:
            self.online = False
        if self.disconnect_sets_hypervisor_isolated:
            self.hypervisor_isolated = True

    def hypervisor_network_isolated(self) -> bool:
        self.hypervisor_checks += 1
        return self.hypervisor_isolated

    def network_online(self):
        self.online_checks += 1
        return self.online


def _ok_handler(argv, cwd, timeout):
    if argv[:2] == ["git", "clone"]:
        return GuestCommandResult(0)
    if argv[:3] == ["git", "-C", WS] and argv[3] == "rev-parse":
        return GuestCommandResult(0, COMMIT + "\n")
    return GuestCommandResult(0, "", "")


def test_successful_build_returns_built_and_compatible():
    transport = FakeTransport(handler=_ok_handler)
    result = run_guest_build(_spec(), transport)
    assert result.status == STATUS_BUILT
    assert result.classification == CLASS_COMPATIBLE
    assert result.workspace == WS
    assert result.failure == ""
    joined = [" ".join(call[0]) for call in transport.calls]
    assert any(cmd.startswith("git clone") for cmd in joined)
    assert any("checkout" in cmd for cmd in joined)
    assert any("rev-parse" in cmd for cmd in joined)
    assert any("apply" in cmd for cmd in joined)
    assert any("pip install" in cmd for cmd in joined)
    assert any("build" in cmd for cmd in joined)


def test_disk_low_fails_before_any_command():
    transport = FakeTransport(free=10, handler=_ok_handler)
    result = run_guest_build(_spec(), transport)
    assert result.status == STATUS_INFRASTRUCTURE_FAILURE
    assert result.reason_code == "DISK_LOW"
    assert transport.calls == []


def test_revision_mismatch_refuses_before_install():
    def handler(argv, cwd, timeout):
        if argv[:3] == ["git", "-C", WS] and argv[3] == "rev-parse":
            return GuestCommandResult(0, "c" * 40 + "\n")
        return GuestCommandResult(0)

    transport = FakeTransport(handler=handler)
    result = run_guest_build(_spec(), transport)
    assert result.status == STATUS_INFRASTRUCTURE_FAILURE
    assert result.reason_code == "SOURCE_REVISION_MISMATCH"
    assert not any("pip install" in " ".join(c[0]) for c in transport.calls)


def test_patch_digest_mismatch_refuses_before_apply():
    transport = FakeTransport(handler=_ok_handler, hashes={PATCH: "d" * 64})
    result = run_guest_build(_spec(), transport)
    assert result.status == STATUS_INFRASTRUCTURE_FAILURE
    assert result.reason_code == "PATCH_DIGEST_MISMATCH"
    assert not any("apply" in " ".join(c[0]) for c in transport.calls)


def test_install_timeout_is_timeout_not_compatible():
    def handler(argv, cwd, timeout):
        if argv[:3] == ["git", "-C", WS] and argv[3] == "rev-parse":
            return GuestCommandResult(0, COMMIT + "\n")
        if "pip" in argv:
            return GuestCommandResult(1, "", "timed out", timed_out=True)
        return GuestCommandResult(0)

    result = run_guest_build(_spec(), FakeTransport(handler=handler))
    assert result.status == STATUS_TIMEOUT
    assert result.reason_code == "INSTALL_TIMEOUT"
    assert result.classification == CLASS_BUILD_FAILURE


def test_install_failure_is_build_failure_not_compatible():
    def handler(argv, cwd, timeout):
        if argv[:3] == ["git", "-C", WS] and argv[3] == "rev-parse":
            return GuestCommandResult(0, COMMIT + "\n")
        if "pip" in argv:
            return GuestCommandResult(1, "", "dependency conflict")
        return GuestCommandResult(0)

    result = run_guest_build(_spec(), FakeTransport(handler=handler))
    assert result.status == STATUS_BUILD_FAILURE
    assert result.reason_code == "INSTALL_FAILED"
    assert result.classification == CLASS_BUILD_FAILURE
    assert "dependency conflict" in result.stderr_tail


def test_build_failure_captures_stderr():
    def handler(argv, cwd, timeout):
        if argv[:3] == ["git", "-C", WS] and argv[3] == "rev-parse":
            return GuestCommandResult(0, COMMIT + "\n")
        if argv[:2] == ["python", "-m"] and "build" in argv:
            return GuestCommandResult(2, "", "build backend exploded")
        return GuestCommandResult(0)

    result = run_guest_build(_spec(), FakeTransport(handler=handler))
    assert result.status == STATUS_BUILD_FAILURE
    assert result.reason_code == "BUILD_FAILED"
    assert "build backend exploded" in result.stderr_tail


def test_invalid_spec_is_refused():
    with pytest.raises(BuildContractError):
        run_guest_build(_spec(repo_url="https://example.com/x.git"), FakeTransport())
    with pytest.raises(BuildContractError):
        run_guest_build(_spec(base_commit="not-a-commit"), FakeTransport())
    with pytest.raises(BuildContractError):
        run_guest_build(_spec(test_patch_sha256="short"), FakeTransport())


def test_transport_timeout_is_classified_as_timeout():
    class TimingOutTransport(FakeTransport):
        def run(self, argv, *, cwd, timeout):
            raise TimeoutError("guest channel timed out")

    result = run_guest_build(_spec(), TimingOutTransport())
    assert result.status == STATUS_TIMEOUT
    assert result.reason_code == "TRANSPORT_TIMEOUT"
    assert result.classification == CLASS_BUILD_FAILURE


def test_transport_crash_is_infrastructure_failure():
    class CrashingTransport(FakeTransport):
        def run(self, argv, *, cwd, timeout):
            raise ConnectionError("VMBus session died")

    result = run_guest_build(_spec(), CrashingTransport())
    assert result.status == STATUS_INFRASTRUCTURE_FAILURE
    assert result.reason_code == "TRANSPORT_FAILURE"
    assert "ConnectionError" in result.stderr_tail
    assert result.classification == CLASS_BUILD_FAILURE


def test_missing_guest_prerequisite_is_infrastructure_failure():
    class NoPatchTransport(FakeTransport):
        def sha256(self, path):
            raise BuildPrerequisiteError("guest patch missing")

    result = run_guest_build(_spec(), NoPatchTransport(handler=_ok_handler))
    assert result.status == STATUS_INFRASTRUCTURE_FAILURE
    assert result.reason_code == "PREREQUISITE_UNAVAILABLE"
    assert result.classification == CLASS_BUILD_FAILURE


def test_failure_duration_uses_the_injected_clock():
    ticks = iter([100.0, 107.5])

    def clock():
        return next(ticks)

    class CrashingTransport(FakeTransport):
        def run(self, argv, *, cwd, timeout):
            raise ConnectionError("boom")

    result = run_guest_build(_spec(), CrashingTransport(), clock=clock)
    assert result.duration_seconds == pytest.approx(7.5)


def _write_task(tmp_path, *, repo="acme/widget", commit=COMMIT):
    task = tmp_path / "task.json"
    task.write_text(json.dumps({"repo": repo, "base_commit": commit}), encoding="utf-8")
    patch = tmp_path / "widget.test_patch"
    patch.write_bytes(b"diff --git a/x b/x\n")
    return task, patch, hashlib.sha256(patch.read_bytes()).hexdigest()


def test_precheck_collection_accepts_consistent_inputs(tmp_path):
    task, patch, digest = _write_task(tmp_path)
    errors = precheck_collection(
        task_id=TASK,
        task_json=task,
        test_patch=patch,
        test_patch_sha256=digest,
        repo_url="https://github.com/acme/widget.git",
        base_commit=COMMIT,
    )
    assert errors == []


def test_precheck_collection_rejects_missing_and_mismatched_inputs(tmp_path):
    task, patch, digest = _write_task(tmp_path)
    errors = precheck_collection(
        task_id=TASK,
        task_json=tmp_path / "missing.json",
        test_patch=tmp_path / "missing.patch",
        test_patch_sha256="e" * 64,
        repo_url="https://github.com/acme/widget.git",
        base_commit=COMMIT,
    )
    assert any("task record missing" in e for e in errors)
    assert any("test patch missing" in e for e in errors)

    errors = precheck_collection(
        task_id=TASK,
        task_json=task,
        test_patch=patch,
        test_patch_sha256="e" * 64,
        repo_url="https://github.com/acme/widget.git",
        base_commit=COMMIT,
    )
    assert any("digest mismatch" in e for e in errors)

    errors = precheck_collection(
        task_id=TASK,
        task_json=task,
        test_patch=patch,
        test_patch_sha256=digest,
        repo_url="https://github.com/other/repo.git",
        base_commit=COMMIT,
    )
    assert any("repo does not match" in e for e in errors)

    mismatched_embedded = tmp_path / "mismatched_embedded.json"
    mismatched_embedded.write_text(
        json.dumps({"repo": "acme/widget", "base_commit": COMMIT, "test_patch": "diff --git a/bad b/bad\n"}),
        encoding="utf-8",
    )
    errors = precheck_collection(
        task_id=TASK,
        task_json=mismatched_embedded,
        test_patch=patch,
        test_patch_sha256=digest,
        repo_url="https://github.com/acme/widget.git",
        base_commit=COMMIT,
    )
    assert any("embedded task record test_patch does not match" in e for e in errors)


def _entry(tmp_path, rel, data=b"x"):
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return ArtifactEntry(rel, len(data), hashlib.sha256(data).hexdigest())


def test_artifact_tree_accepts_valid_files(tmp_path):
    entry = _entry(tmp_path, "out/report.json", b"{}")
    result = validate_artifact_tree(tmp_path, [entry])
    assert result == (entry,)


@pytest.mark.parametrize("rel", ["../escape", "/abs", "C:/abs", "out/../../escape"])
def test_artifact_tree_rejects_traversal_and_absolute(tmp_path, rel):
    entry = ArtifactEntry(rel, 1, "0" * 64)
    with pytest.raises(BuildContractError):
        validate_artifact_tree(tmp_path, [entry])


def test_artifact_tree_rejects_duplicates_and_limits(tmp_path):
    one = _entry(tmp_path, "a.txt", b"a")
    two = _entry(tmp_path, "b.txt", b"b")
    with pytest.raises(BuildContractError, match="duplicate"):
        validate_artifact_tree(tmp_path, [one, one])
    with pytest.raises(BuildContractError, match="count"):
        validate_artifact_tree(tmp_path, [one, two], max_files=1)
    with pytest.raises(BuildContractError, match="bytes"):
        validate_artifact_tree(tmp_path, [one, two], max_bytes=1)


def test_artifact_tree_rejects_digest_and_size_mismatch(tmp_path):
    real = _entry(tmp_path, "a.txt", b"abc")
    with pytest.raises(BuildContractError, match="size"):
        validate_artifact_tree(tmp_path, [ArtifactEntry("a.txt", 99, real.sha256)])
    with pytest.raises(BuildContractError, match="digest"):
        validate_artifact_tree(tmp_path, [ArtifactEntry("a.txt", 3, "f" * 64)])


def test_artifact_tree_rejects_symlink(tmp_path):
    target = tmp_path / "target.txt"
    target.write_text("x", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not available")
    entry = ArtifactEntry("link.txt", 1, hashlib.sha256(b"x").hexdigest())
    with pytest.raises(BuildContractError, match="reparse|symlink"):
        validate_artifact_tree(tmp_path, [entry])


def test_atomic_publish_refuses_existing_target(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "a").write_text("x", encoding="utf-8")
    final = tmp_path / "final"
    publish_artifacts_atomically(staging, final)
    assert final.is_dir() and not staging.exists()
    staging2 = tmp_path / "staging2"
    staging2.mkdir()
    with pytest.raises(BuildContractError, match="already exists"):
        publish_artifacts_atomically(staging2, final)


def test_atomic_publish_creates_missing_parent(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "a").write_text("x", encoding="utf-8")
    final = tmp_path / "missing" / "parent" / "final"
    publish_artifacts_atomically(staging, final)
    assert final.is_dir()
    assert (final / "a").is_file()


@pytest.mark.skipif(os.name != "nt", reason="Windows drive semantics")
def test_atomic_publish_refuses_cross_volume(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    with pytest.raises(BuildContractError, match="cross-volume"):
        publish_artifacts_atomically(staging, "D:\\s8-does-not-exist\\final")


@pytest.mark.skipif(os.name != "nt", reason="NTFS is case-insensitive")
def test_artifact_tree_rejects_case_insensitive_alias(tmp_path):
    data = b"same bytes"
    path = tmp_path / "artifact.txt"
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    entries = [
        ArtifactEntry("artifact.txt", len(data), digest),
        ArtifactEntry("ARTIFACT.TXT", len(data), digest),
    ]
    with pytest.raises(BuildContractError, match="duplicate artifact"):
        validate_artifact_tree(tmp_path, entries)


def test_artifact_tree_rejects_hardlink_alias(tmp_path):
    data = b"same bytes"
    path = tmp_path / "a.txt"
    path.write_bytes(data)
    link = tmp_path / "b.txt"
    try:
        os.link(path, link)
    except (OSError, NotImplementedError):
        pytest.skip("hardlink creation not available")
    digest = hashlib.sha256(data).hexdigest()
    entries = [
        ArtifactEntry("a.txt", len(data), digest),
        ArtifactEntry("b.txt", len(data), digest),
    ]
    with pytest.raises(BuildContractError, match="alias"):
        validate_artifact_tree(tmp_path, entries)


class FakeChannel:
    def __init__(self):
        self.commands = []

    def run(self, command, *, timeout):
        self.commands.append((command, timeout))
        if "Get-FileHash" in command:
            return 0, ("A" * 64) + "\n", ""
        if "Get-PSDrive" in command:
            return 0, "12345\n", ""
        return 0, "out", "err"


def test_channel_transport_builds_powershell_and_parses(tmp_path):
    channel = FakeChannel()
    transport = ChannelBuildTransport(channel)
    result = transport.run(
        ["git", "clone", "https://github.com/acme/widget.git", "C:/ws"],
        cwd="C:/stage",
        timeout=5,
    )
    assert result.returncode == 0
    command, timeout = channel.commands[-1]
    assert "Set-Location -LiteralPath 'C:/stage'" in command
    assert "& 'git' 'clone' 'https://github.com/acme/widget.git' 'C:/ws'" in command
    assert timeout == 5
    assert transport.sha256("C:/patch.diff") == "a" * 64
    assert transport.free_bytes("C:/ws") == 12345


def test_channel_transport_rejects_invalid_guest_hash():
    class BadChannel:
        def run(self, command, *, timeout):
            return 0, "not-a-hash", ""

    with pytest.raises(BuildPrerequisiteError):
        ChannelBuildTransport(BadChannel()).sha256("C:/patch.diff")


def test_channel_transport_sha256_rejects_nonzero_exit_even_with_valid_hash():
    class FailingChannel:
        def run(self, command, *, timeout):
            return 1, "a" * 64, "access denied"

    with pytest.raises(BuildPrerequisiteError):
        ChannelBuildTransport(FailingChannel()).sha256("C:/patch.diff")


@pytest.mark.parametrize(
    "rc,stdout",
    [
        (0, "not-a-number"),
        (0, "-1"),
        (0, ""),
        (1, "12345"),
    ],
)
def test_channel_transport_free_bytes_rejects_malformed_or_failed_reads(rc, stdout):
    class BadChannel:
        def run(self, command, *, timeout):
            return rc, stdout, "rejected"

    with pytest.raises(BuildPrerequisiteError):
        ChannelBuildTransport(BadChannel()).free_bytes("C:/ws")


def test_channel_transport_free_bytes_targets_the_path_drive():
    channel = FakeChannel()
    assert ChannelBuildTransport(channel).free_bytes("D:/build/ws") == 12345
    command, _timeout = channel.commands[-1]
    assert "Get-PSDrive -Name 'D'" in command


def test_channel_transport_run_reports_timeout_as_structured_result():
    class TimingOutChannel:
        def run(self, command, *, timeout):
            raise subprocess.TimeoutExpired(cmd="pwsh", timeout=timeout)

    result = ChannelBuildTransport(TimingOutChannel()).run(
        ["git", "status"], cwd="C:/ws", timeout=5
    )
    assert result.timed_out is True
    assert result.returncode == -1
    assert "timed out after 5s" in result.stderr


def test_channel_transport_hash_timeout_propagates_as_timeout():
    class TimingOutChannel:
        def run(self, command, *, timeout):
            raise subprocess.TimeoutExpired(cmd="pwsh", timeout=timeout)

    with pytest.raises(subprocess.TimeoutExpired):
        ChannelBuildTransport(TimingOutChannel()).sha256("C:/patch.diff")


def test_guest_hash_timeout_is_classified_as_timeout():
    class HashTimeoutTransport(FakeTransport):
        def sha256(self, path):
            raise subprocess.TimeoutExpired(cmd="pwsh", timeout=60)

    result = run_guest_build(
        _spec(), HashTimeoutTransport(handler=_ok_handler)
    )
    assert result.status == STATUS_TIMEOUT
    assert result.reason_code == "TRANSPORT_TIMEOUT"


def _valid_readiness(**over):
    base = {
        "schema": GUEST_READINESS_SCHEMA,
        "computer_name": "F2-S8-BUILD1",
        "user": "f2builder",
        "is_admin": False,
        "python": "C:\\Tools\\Python313\\python.exe",
        "git": "C:\\Program Files\\Git\\cmd\\git.exe",
        "uv": "C:\\Tools\\uv\\uv.exe",
        "uv_sha256": UV_APPROVED_SHA256,
        "free_bytes": 50_000_000_000,
    }
    base.update(over)
    return base


def test_preflight_build_environment_accepts_valid_configuration(tmp_path):
    vhd = tmp_path / "base.vhdx"
    vhd.write_bytes(b"synthetic vhdx payload")
    vhd_sha = hashlib.sha256(vhd.read_bytes()).hexdigest()
    readiness = tmp_path / "readiness.json"
    readiness.write_text(json.dumps(_valid_readiness()), encoding="utf-8")

    res = preflight_build_environment(
        vm_name="F2-S8-BUILD1",
        network_policy="CONTROLLED_ACQUISITION_OFFLINE_EXECUTION",
        readiness_report=readiness,
        media_path=vhd,
        expected_media_sha256=vhd_sha,
        configured_switch="Default Switch",
        phase_disconnect_verified=True,
    )
    assert res.vm_name == "F2-S8-BUILD1"
    assert res.media_sha256 == vhd_sha
    assert res.guest_user == "f2builder"
    assert res.uv_sha256 == UV_APPROVED_SHA256
    assert res.network_isolation_status == "ENFORCED_OFFLINE"


@pytest.mark.parametrize("protected_vm", ["F2-S8-VM1", "f2-s8-vm1", "F2-Isolation-VM"])
def test_preflight_build_environment_refuses_protected_vms(protected_vm):
    with pytest.raises(BuildPrerequisiteError, match="protected execution VM"):
        preflight_build_environment(
            vm_name=protected_vm,
            network_policy="OFFLINE_WHEELHOUSE_ONLY",
            readiness_report=_valid_readiness(),
        )
    with pytest.raises(BuildPrerequisiteError, match="protected execution VM"):
        preflight_build_environment(
            vm_name="F2-S8-BUILD1",
            network_policy="OFFLINE_WHEELHOUSE_ONLY",
            readiness_report=_valid_readiness(computer_name=protected_vm),
        )


def test_preflight_build_environment_checks_media_storage_toolchain_and_network(tmp_path):
    vhd = tmp_path / "base.vhdx"
    vhd.write_bytes(b"real media")
    # Wrong media hash
    with pytest.raises(BuildPrerequisiteError, match="media SHA-256 mismatch"):
        preflight_build_environment(
            vm_name="F2-S8-BUILD1",
            network_policy="OFFLINE_WHEELHOUSE_ONLY",
            readiness_report=_valid_readiness(),
            media_path=vhd,
            expected_media_sha256="0" * 64,
        )
    # Forbidden internal switch
    with pytest.raises(BuildPrerequisiteError, match="forbidden"):
        preflight_build_environment(
            vm_name="F2-S8-BUILD1",
            network_policy="CONTROLLED_ACQUISITION_OFFLINE_EXECUTION",
            readiness_report=_valid_readiness(),
            configured_switch="F2-Internal-Switch",
        )
    # Offline policy with external switch attached
    with pytest.raises(BuildPrerequisiteError, match="OFFLINE_WHEELHOUSE_ONLY forbids"):
        preflight_build_environment(
            vm_name="F2-S8-BUILD1",
            network_policy="OFFLINE_WHEELHOUSE_ONLY",
            readiness_report=_valid_readiness(),
            configured_switch="Default Switch",
        )
    # Controlled acquisition on Default Switch WITHOUT verified phase disconnect is refused
    with pytest.raises(
        BuildPrerequisiteError, match="NETWORK_ISOLATION_NOT_ESTABLISHED"
    ):
        preflight_build_environment(
            vm_name="F2-S8-BUILD1",
            network_policy="CONTROLLED_ACQUISITION_OFFLINE_EXECUTION",
            readiness_report=_valid_readiness(),
            configured_switch="Default Switch",
            phase_disconnect_verified=False,
        )
    # Privileged guest user
    with pytest.raises(BuildPrerequisiteError, match="unprivileged"):
        preflight_build_environment(
            vm_name="F2-S8-BUILD1",
            network_policy="OFFLINE_WHEELHOUSE_ONLY",
            readiness_report=_valid_readiness(is_admin=True),
        )
    # Wrong uv digest
    with pytest.raises(BuildPrerequisiteError, match="uv_sha256 mismatch"):
        preflight_build_environment(
            vm_name="F2-S8-BUILD1",
            network_policy="OFFLINE_WHEELHOUSE_ONLY",
            readiness_report=_valid_readiness(uv_sha256="a" * 64),
        )
    # Low guest disk space
    with pytest.raises(BuildPrerequisiteError, match="below required"):
        preflight_build_environment(
            vm_name="F2-S8-BUILD1",
            network_policy="OFFLINE_WHEELHOUSE_ONLY",
            readiness_report=_valid_readiness(free_bytes=10_000_000_000),
        )


def test_end_to_end_synthetic_build_artifact_publication_and_probe(tmp_path):
    """Harmless end-to-end synthetic build, artifact validation, atomic publication,
    and probe driver target stop."""
    from qwen_train.f2_s8_probe_driver import ProbeLimits, run_probe

    # 1. Host precheck collection
    task_json, patch_path, patch_sha = _write_task(tmp_path)
    assert (
        precheck_collection(
            task_id=TASK,
            task_json=task_json,
            test_patch=patch_path,
            test_patch_sha256=patch_sha,
            repo_url="https://github.com/acme/widget.git",
            base_commit=COMMIT,
        )
        == []
    )

    # 2. Build environment preflight
    preflight_build_environment(
        vm_name="F2-S8-BUILD1",
        network_policy="CONTROLLED_ACQUISITION_OFFLINE_EXECUTION",
        readiness_report=_valid_readiness(),
    )

    # 3. Run synthetic build + validate + publish inside probe builder callback
    staging_root = tmp_path / "staging"
    final_root = tmp_path / "published"

    def synthetic_builder(batch):
        out = {}
        for tid in batch:
            spec = _spec(
                task_id=tid,
                test_patch=str(patch_path),
                test_patch_sha256=patch_sha,
            )
            transport = FakeTransport(
                handler=_ok_handler, hashes={str(patch_path): patch_sha}
            )
            build_res = run_guest_build(spec, transport)
            assert build_res.status == STATUS_BUILT
            stage_dir = staging_root / tid
            stage_dir.mkdir(parents=True, exist_ok=True)
            wheel_bytes = b"synthetic-wheel-bytes"
            (stage_dir / "dist").mkdir(exist_ok=True)
            (stage_dir / "dist" / "widget-0.1.whl").write_bytes(wheel_bytes)
            manifest_entry = ArtifactEntry(
                "dist/widget-0.1.whl",
                len(wheel_bytes),
                hashlib.sha256(wheel_bytes).hexdigest(),
            )
            validated = validate_artifact_tree(stage_dir, [manifest_entry])
            assert len(validated) == 1
            pub_dir = final_root / tid
            publish_artifacts_atomically(stage_dir, pub_dir)
            out[tid] = {
                "classification": build_res.classification,
                "workspace": str(pub_dir),
            }
        return out

    probe_res = run_probe(
        unknown_task_ids=[TASK, "acme__widget-2"],
        limits=ProbeLimits(
            max_tasks=10,
            disk_floor_bytes=100,
            per_task_disk_bytes=50,
            batch_limit=1,
            target_compatible=1,
        ),
        authorize=lambda: None,
        builder=synthetic_builder,
        free_bytes=lambda: 10**12,
        verify_ledgers=lambda: None,
        log=lambda _: None,
    )
    assert probe_res.stop_reason == "PROBE_TARGET_REACHED"
    assert probe_res.compatible == 1
    assert probe_res.built == 1
    assert (final_root / TASK / "dist" / "widget-0.1.whl").is_file()


def test_1_acquisition_completes_before_network_disconnection():
    """1. Verify two-phase separation: acquisition runs while online, then
    disconnect_network() is enforced and network_online() is verified False
    BEFORE test_patch apply, install_argv, and build_argv execute."""
    events = []

    class PhaseTrackingTransport(FakeTransport):
        def run(self, argv, *, cwd, timeout):
            events.append(("run", argv[0], self.online, self.hypervisor_isolated))
            return super().run(argv, cwd=cwd, timeout=timeout)

        def disconnect_network(self):
            events.append(("disconnect", None, self.online, self.hypervisor_isolated))
            super().disconnect_network()

    transport = PhaseTrackingTransport(
        handler=_ok_handler, online=True, disconnect_clears_online=True
    )
    spec = _spec(
        acquire_argv=("uv", "pip", "download", "--only-binary", ":all:", "-d", "C:/S8Build/in/wheels", "-r", "requirements.txt"),
        network_policy="CONTROLLED_ACQUISITION_OFFLINE_EXECUTION",
        require_offline_build_phase=True,
    )
    res = run_guest_build(spec, transport)
    assert res.status == STATUS_BUILT
    assert res.network_isolated_for_build is True
    assert transport.disconnect_calls >= 1
    assert transport.online is False
    assert transport.hypervisor_isolated is True
    # Acquisition ran while online=True; apply/install/build ran while online=False
    uv_runs = [online for kind, cmd, online, _ in events if kind == "run" and cmd == "uv"]
    py_runs = [online for kind, cmd, online, _ in events if kind == "run" and cmd == "python"]
    assert uv_runs == [True]
    assert py_runs == [False, False]


def test_2_host_disconnect_failure_prevents_candidate_execution():
    """2. Host disconnect failure prevents candidate execution."""
    def fail_disconnect():
        raise RuntimeError("Hyper-V WMI connection dropped")

    transport = FakeTransport(
        handler=_ok_handler,
        online=True,
        disconnect_callback=fail_disconnect,
    )
    spec = _spec(
        network_policy="CONTROLLED_ACQUISITION_OFFLINE_EXECUTION",
        require_offline_build_phase=True,
    )
    res = run_guest_build(spec, transport)
    assert res.status == STATUS_INFRASTRUCTURE_FAILURE
    assert res.reason_code == "NETWORK_ISOLATION_NOT_ESTABLISHED"
    assert "disconnect_network() failed" in res.stderr_tail
    assert not any("apply" in " ".join(c[0]) for c in transport.calls)
    assert not any("pip" in " ".join(c[0]) for c in transport.calls)


def test_3_guest_remains_online_fails_closed():
    """3. Guest remains online after disconnect attempt fails closed."""
    transport = FakeTransport(
        handler=_ok_handler,
        online=True,
        disconnect_clears_online=False,
        hypervisor_isolated=True,
    )
    spec = _spec(
        network_policy="CONTROLLED_ACQUISITION_OFFLINE_EXECUTION",
        require_offline_build_phase=True,
    )
    res = run_guest_build(spec, transport)
    assert res.status == STATUS_INFRASTRUCTURE_FAILURE
    assert res.reason_code == "NETWORK_ISOLATION_NOT_ESTABLISHED"
    assert "guest network remains online" in res.stderr_tail
    assert not any("apply" in " ".join(c[0]) for c in transport.calls)
    assert not any("pip" in " ".join(c[0]) for c in transport.calls)


def test_4_guest_offline_but_host_unverified_fails_closed():
    """4. Guest reports offline but host-side disconnection is unverified."""
    transport = FakeTransport(
        handler=_ok_handler,
        online=False,
        hypervisor_isolated=False,
        disconnect_sets_hypervisor_isolated=False,
    )
    spec = _spec(
        network_policy="CONTROLLED_ACQUISITION_OFFLINE_EXECUTION",
        require_offline_build_phase=True,
    )
    res = run_guest_build(spec, transport)
    assert res.status == STATUS_INFRASTRUCTURE_FAILURE
    assert res.reason_code == "NETWORK_ISOLATION_NOT_ESTABLISHED"
    assert "host hypervisor reports virtual NIC still connected" in res.stderr_tail
    assert not any("apply" in " ".join(c[0]) for c in transport.calls)


def test_5_missing_isolation_verification_fails_closed():
    """5. Missing or stale isolation verification fails closed."""
    class NoHypervisorTransport(FakeTransport):
        hypervisor_network_isolated = None  # Not callable

    t1 = NoHypervisorTransport(handler=_ok_handler, online=False)
    spec = _spec(
        network_policy="CONTROLLED_ACQUISITION_OFFLINE_EXECUTION",
        require_offline_build_phase=True,
    )
    res1 = run_guest_build(spec, t1)
    assert res1.status == STATUS_INFRASTRUCTURE_FAILURE
    assert res1.reason_code == "NETWORK_ISOLATION_NOT_ESTABLISHED"
    assert "cannot verify host-side hypervisor_network_isolated()" in res1.stderr_tail

    class NoGuestOnlineTransport(FakeTransport):
        network_online = None  # Not callable

    t2 = NoGuestOnlineTransport(handler=_ok_handler, online=False)
    res2 = run_guest_build(spec, t2)
    assert res2.status == STATUS_INFRASTRUCTURE_FAILURE
    assert res2.reason_code == "NETWORK_ISOLATION_NOT_ESTABLISHED"
    assert "cannot verify guest network_online()" in res2.stderr_tail


def test_6_host_disconnect_exception_and_timeout_fail_closed():
    """6. Exceptions and timeouts in host disconnect callback fail closed."""
    class TimeoutHypervisorTransport(FakeTransport):
        def hypervisor_network_isolated(self):
            raise subprocess.TimeoutExpired(cmd="powershell", timeout=60)

    t1 = TimeoutHypervisorTransport(handler=_ok_handler, online=False)
    spec = _spec(
        network_policy="CONTROLLED_ACQUISITION_OFFLINE_EXECUTION",
        require_offline_build_phase=True,
    )
    res1 = run_guest_build(spec, t1)
    assert res1.status == STATUS_INFRASTRUCTURE_FAILURE
    assert res1.reason_code == "NETWORK_ISOLATION_NOT_ESTABLISHED"

    class ErrorGuestOnlineTransport(FakeTransport):
        def network_online(self):
            raise RuntimeError("VMBus pipe broken")

    t2 = ErrorGuestOnlineTransport(handler=_ok_handler, online=False)
    res2 = run_guest_build(spec, t2)
    assert res2.status == STATUS_INFRASTRUCTURE_FAILURE
    assert res2.reason_code == "NETWORK_ISOLATION_NOT_ESTABLISHED"


def test_7_isolation_checked_before_every_candidate_phase():
    """7. Isolation checks immediately before every candidate-controlled phase
    (test_patch apply, install_argv, and build_argv)."""
    # Scenario A: network comes back up right before install_argv
    class ReconnectedBeforeInstall(FakeTransport):
        def run(self, argv, *, cwd, timeout):
            if argv[:2] == ["git", "-C"] and argv[3] == "apply":
                self.online = True
                self.disconnect_clears_online = False
            return super().run(argv, cwd=cwd, timeout=timeout)

    t_a = ReconnectedBeforeInstall(handler=_ok_handler, online=True, disconnect_clears_online=True)
    spec = _spec(
        network_policy="CONTROLLED_ACQUISITION_OFFLINE_EXECUTION",
        require_offline_build_phase=True,
    )
    res_a = run_guest_build(spec, t_a)
    assert res_a.status == STATUS_INFRASTRUCTURE_FAILURE
    assert res_a.reason_code == "NETWORK_ISOLATION_NOT_ESTABLISHED"
    joined_a = [" ".join(c[0]) for c in t_a.calls]
    assert any("apply" in cmd for cmd in joined_a)
    assert not any("pip install" in cmd for cmd in joined_a)
    assert not any("-m build" in cmd for cmd in joined_a)

    # Scenario B: network comes back up right before build_argv
    class ReconnectedBeforeBuild(FakeTransport):
        def run(self, argv, *, cwd, timeout):
            if list(argv[:3]) == ["python", "-m", "pip"]:
                self.hypervisor_isolated = False
                self.disconnect_sets_hypervisor_isolated = False
            return super().run(argv, cwd=cwd, timeout=timeout)

    t_b = ReconnectedBeforeBuild(handler=_ok_handler, online=True, disconnect_clears_online=True)
    res_b = run_guest_build(spec, t_b)
    assert res_b.status == STATUS_INFRASTRUCTURE_FAILURE
    assert res_b.reason_code == "NETWORK_ISOLATION_NOT_ESTABLISHED"
    joined_b = [" ".join(c[0]) for c in t_b.calls]
    assert any("pip install" in cmd for cmd in joined_b)
    assert not any("-m build" in cmd for cmd in joined_b)


def test_8_refusal_to_touch_protected_vms():
    """8. VM identity mismatch (refusal to touch protected VMs)."""
    channel = FakeChannel()
    for vm in ("F2-S8-VM1", "F2-Isolation-VM", "f2-s8-vm1"):
        with pytest.raises(BuildPrerequisiteError, match="refusing to bind transport to protected"):
            ChannelBuildTransport(channel, vm_name=vm)

    with pytest.raises(BuildPrerequisiteError, match="refusing to use protected execution VM"):
        preflight_build_environment(
            vm_name="F2-S8-VM1",
            network_policy="CONTROLLED_ACQUISITION_OFFLINE_EXECUTION",
            readiness_report={
                "schema": GUEST_READINESS_SCHEMA,
                "user": "builder",
                "uv_sha256": UV_APPROVED_SHA256,
                "free_bytes": 30_000_000_000,
                "python": "p",
                "git": "g",
                "uv": "u",
            },
            phase_disconnect_verified=True,
        )


def test_9_no_reconnection_during_candidate_execution():
    """9. VM is not reconnected while candidate-controlled processes remain active."""
    post_disconnect_states = []

    class ReconnectAuditingTransport(FakeTransport):
        def run(self, argv, *, cwd, timeout):
            if self.disconnect_calls > 0:
                post_disconnect_states.append((argv, self.online, self.hypervisor_isolated))
            return super().run(argv, cwd=cwd, timeout=timeout)

    transport = ReconnectAuditingTransport(
        handler=_ok_handler, online=True, disconnect_clears_online=True
    )
    spec = _spec(
        acquire_argv=("uv", "pip", "download", "--only-binary", ":all:", "-d", "C:/S8Build/in/wheels", "-r", "requirements.txt"),
        network_policy="CONTROLLED_ACQUISITION_OFFLINE_EXECUTION",
        require_offline_build_phase=True,
    )
    res = run_guest_build(spec, transport)
    assert res.status == STATUS_BUILT
    assert len(post_disconnect_states) >= 3  # patch apply, pip install, build
    assert all(online is False and isolated is True for _, online, isolated in post_disconnect_states)


def test_10_missing_dependencies_offline_fails_safely():
    """10. Missing dependencies during offline execution fail safely."""
    def fail_on_install(argv, cwd, timeout):
        if argv[:2] == ["git", "clone"]:
            return GuestCommandResult(0)
        if argv[:3] == ["git", "-C", WS] and argv[3] == "rev-parse":
            return GuestCommandResult(0, COMMIT + "\n")
        if argv[:3] == ["git", "-C", WS] and argv[3] == "apply":
            return GuestCommandResult(0)
        if argv[:2] == ["python", "-m"] and argv[2] == "pip":
            return GuestCommandResult(1, stdout="", stderr="ERROR: No matching distribution found for foo-bar")
        return GuestCommandResult(0)

    transport = FakeTransport(
        handler=fail_on_install,
        online=False,
        hypervisor_isolated=True,
    )
    spec = _spec(
        install_argv=("python", "-m", "pip", "install", "--no-index", "--find-links", "C:/wheels", "-r", "requirements.txt"),
        network_policy="CONTROLLED_ACQUISITION_OFFLINE_EXECUTION",
        require_offline_build_phase=True,
    )
    res = run_guest_build(spec, transport)
    assert res.status == STATUS_BUILD_FAILURE
    assert res.reason_code == "INSTALL_FAILED"
    assert "No matching distribution found" in res.stderr_tail
    assert transport.online is False
    assert transport.hypervisor_isolated is True


def test_11_no_fallback_to_host_execution(monkeypatch):
    """11. No fallback to host-side candidate execution."""
    host_subprocess_calls = []
    real_subprocess_run = subprocess.run

    def audit_run(cmd, *args, **kwargs):
        host_subprocess_calls.append(cmd)
        return real_subprocess_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", audit_run)

    class CrashingTransport(FakeTransport):
        def run(self, argv, *, cwd, timeout):
            if argv[:2] == ["git", "clone"]:
                raise ConnectionResetError("guest transport pipe broken")
            return super().run(argv, cwd=cwd, timeout=timeout)

    transport = CrashingTransport(handler=_ok_handler, online=False)
    spec = _spec(
        network_policy="CONTROLLED_ACQUISITION_OFFLINE_EXECUTION",
        require_offline_build_phase=True,
    )
    res = run_guest_build(spec, transport)
    assert res.status == STATUS_INFRASTRUCTURE_FAILURE
    assert res.reason_code == "TRANSPORT_FAILURE"
    assert not any("git" in str(cmd) for cmd in host_subprocess_calls)
    assert not any("pip" in str(cmd) for cmd in host_subprocess_calls)
    assert not any("build" in str(cmd) for cmd in host_subprocess_calls)


def test_cli_main_missing_grant_fails(capsys, tmp_path):
    """CLI fails closed when grant file does not exist."""
    rc = main(["--grant-file", str(tmp_path / "nonexistent_grant.json")])
    assert rc == 2
    captured = capsys.readouterr()
    assert "GRANT_NOT_FOUND" in captured.err


def test_cli_main_protected_vm_refusal(capsys, tmp_path, monkeypatch):
    """CLI refuses to execute against protected VMs."""
    grant_file = tmp_path / "dummy_grant.json"
    grant_file.write_text("{}", encoding="utf-8")

    from unittest.mock import MagicMock
    mock_auth = MagicMock()
    mock_auth.selected_task_ids = {"openai__openai-agents-python-1619"}
    mock_auth.probe_pool_digest = "a" * 64
    mock_auth.probe_selection_digest = "b" * 64
    mock_auth.network_policy = "CONTROLLED_ACQUISITION_OFFLINE_EXECUTION"
    mock_auth.budget_limits = {"max_total_tasks": 1}
    mock_auth.batch_limit = 1
    mock_auth.target_compatible = 1

    import qwen_train.f2_s8_v2_gate
    monkeypatch.setattr(qwen_train.f2_s8_v2_gate, "authorize_s8_probe_run", lambda **kwargs: mock_auth)

    rc = main([
        "--grant-file", str(grant_file),
        "--vm-name", "F2-S8-VM1",
        "--dry-run",
        "--authorized-acquisition-policy", "CONTROLLED_ACQUISITION_OFFLINE_EXECUTION",
    ])
    assert rc == 2
    captured = capsys.readouterr()
    assert "SECURITY_REFUSAL" in captured.err


def test_cli_main_missing_acquisition_policy_refusal(capsys, tmp_path, monkeypatch):
    """CLI refuses to execute if controlled acquisition is not explicitly authorized."""
    grant_file = tmp_path / "dummy_grant.json"
    grant_file.write_text("{}", encoding="utf-8")

    from unittest.mock import MagicMock
    mock_auth = MagicMock()
    mock_auth.selected_task_ids = {"openai__openai-agents-python-1619"}
    mock_auth.probe_pool_digest = "a" * 64
    mock_auth.probe_selection_digest = "b" * 64
    mock_auth.network_policy = "CONTROLLED_ACQUISITION_OFFLINE_EXECUTION"
    mock_auth.budget_limits = {"max_total_tasks": 1}
    mock_auth.batch_limit = 1
    mock_auth.target_compatible = 1

    import qwen_train.f2_s8_v2_gate
    monkeypatch.setattr(qwen_train.f2_s8_v2_gate, "authorize_s8_probe_run", lambda **kwargs: mock_auth)

    rc = main([
        "--grant-file", str(grant_file),
        "--vm-name", "F2-S8-BUILD1",
        "--dry-run",
        "--network-policy", "CONTROLLED_ACQUISITION_OFFLINE_EXECUTION",
        "--authorized-acquisition-policy", "",
    ])
    assert rc == 2
    captured = capsys.readouterr()
    assert "POLICY_REFUSAL" in captured.err


def test_cli_main_missing_inputs_refusal(capsys, tmp_path, monkeypatch):
    """CLI fails closed when task input JSON or patch is missing."""
    grant_file = tmp_path / "dummy_grant.json"
    grant_file.write_text("{}", encoding="utf-8")

    from unittest.mock import MagicMock
    mock_auth = MagicMock()
    mock_auth.selected_task_ids = {"openai__openai-agents-python-1619"}
    mock_auth.probe_pool_digest = "a" * 64
    mock_auth.probe_selection_digest = "b" * 64
    mock_auth.network_policy = "CONTROLLED_ACQUISITION_OFFLINE_EXECUTION"
    mock_auth.budget_limits = {"max_total_tasks": 1}
    mock_auth.batch_limit = 1
    mock_auth.target_compatible = 1

    import qwen_train.f2_s8_v2_gate
    monkeypatch.setattr(qwen_train.f2_s8_v2_gate, "authorize_s8_probe_run", lambda **kwargs: mock_auth)

    inputs_dir = tmp_path / "inputs"
    inputs_dir.mkdir()
    patches_dir = tmp_path / "patches"
    patches_dir.mkdir()

    rc = main([
        "--grant-file", str(grant_file),
        "--vm-name", "F2-S8-BUILD1",
        "--dry-run",
        "--authorized-acquisition-policy", "CONTROLLED_ACQUISITION_OFFLINE_EXECUTION",
        "--inputs-dir", str(inputs_dir),
        "--patches-dir", str(patches_dir),
    ])
    assert rc == 2
    captured = capsys.readouterr()
    assert "INPUT_MISSING" in captured.err


def test_cli_main_dry_run_success(capsys, tmp_path, monkeypatch):
    """CLI --dry-run validates inputs and completes cleanly."""
    grant_file = tmp_path / "dummy_grant.json"
    grant_file.write_text("{}", encoding="utf-8")

    from unittest.mock import MagicMock
    mock_auth = MagicMock()
    mock_auth.selected_task_ids = {"openai__openai-agents-python-1619"}
    mock_auth.probe_pool_digest = "a" * 64
    mock_auth.probe_selection_digest = "b" * 64
    mock_auth.network_policy = "CONTROLLED_ACQUISITION_OFFLINE_EXECUTION"
    mock_auth.budget_limits = {"max_total_tasks": 1}
    mock_auth.batch_limit = 1
    mock_auth.target_compatible = 1

    import qwen_train.f2_s8_v2_gate
    monkeypatch.setattr(qwen_train.f2_s8_v2_gate, "authorize_s8_probe_run", lambda **kwargs: mock_auth)

    inputs_dir = tmp_path / "inputs"
    inputs_dir.mkdir()
    patches_dir = tmp_path / "patches"
    patches_dir.mkdir()

    task_json = inputs_dir / "openai__openai-agents-python-1619.json"
    task_json.write_text(json.dumps({
        "repo": "openai/openai-agents-python",
        "base_commit": "5de3b5849ed973fa09b0e78fbe2a65f1b3073035",
    }), encoding="utf-8")

    test_patch = patches_dir / "openai__openai-agents-python-1619.test_patch"
    test_patch.write_bytes(b"diff --git a/test b/test\n")

    rc = main([
        "--grant-file", str(grant_file),
        "--vm-name", "F2-S8-BUILD1",
        "--dry-run",
        "--authorized-acquisition-policy", "CONTROLLED_ACQUISITION_OFFLINE_EXECUTION",
        "--inputs-dir", str(inputs_dir),
        "--patches-dir", str(patches_dir),
    ])
    assert rc == 0
    captured = capsys.readouterr()
    assert "[DRY RUN] Spec validated" in captured.out


def test_cli_main_verify_grant_only(capsys, tmp_path, monkeypatch):
    """CLI --verify-grant-only performs authorization check and emits json."""
    grant_file = tmp_path / "dummy_grant.json"
    grant_file.write_text("{}", encoding="utf-8")

    from unittest.mock import MagicMock
    mock_auth = MagicMock()
    mock_auth.selected_task_ids = {"openai__openai-agents-python-1619"}
    mock_auth.probe_pool_digest = "a" * 64
    mock_auth.probe_selection_digest = "b" * 64
    mock_auth.network_policy = "CONTROLLED_ACQUISITION_OFFLINE_EXECUTION"
    mock_auth.budget_limits = {"max_total_tasks": 1}
    mock_auth.batch_limit = 1
    mock_auth.target_compatible = 1

    import qwen_train.f2_s8_v2_gate
    monkeypatch.setattr(qwen_train.f2_s8_v2_gate, "authorize_s8_probe_run", lambda **kwargs: mock_auth)

    rc = main([
        "--grant-file", str(grant_file),
        "--verify-grant-only",
    ])
    assert rc == 0
    captured = capsys.readouterr()
    assert "GRANT_VERIFIED" in captured.out


def test_per_phase_timing_tracking():
    """Verify that every execution phase records non-negative duration and start/end details."""
    transport = FakeTransport(handler=_ok_handler)
    spec = _spec()
    res = run_guest_build(spec, transport)
    assert res.status == STATUS_BUILT
    assert res.classification == CLASS_COMPATIBLE

    expected_phases = ["clone", "checkout", "rev_parse", "patch_verify", "patch_apply", "install", "build"]
    for phase in expected_phases:
        assert phase in res.phase_timings, f"missing timing for {phase}"
        assert res.phase_timings[phase] >= 0.0
        assert phase in res.phase_details, f"missing details for {phase}"
        details = res.phase_details[phase]
        assert "start" in details and "end" in details and "duration" in details
        assert details["end"] >= details["start"]


def test_required_python_version_preflight_success():
    """Python version preflight passes when guest interpreter satisfies task requirement."""
    def py_handler(argv, cwd, timeout):
        if argv[:2] == ["python", "-c"]:
            return GuestCommandResult(0, "3.10.12\n", "")
        return _ok_handler(argv, cwd, timeout)

    transport = FakeTransport(handler=py_handler)
    spec = _spec(required_python="3.10")
    res = run_guest_build(spec, transport)
    assert res.status == STATUS_BUILT
    assert "python_preflight" in res.phase_timings


def test_required_python_version_preflight_mismatch_fails_closed():
    """Python version preflight fails closed when guest interpreter version does not match."""
    def py_handler(argv, cwd, timeout):
        if argv[:2] == ["python", "-c"]:
            return GuestCommandResult(0, "3.13.0\n", "")
        return _ok_handler(argv, cwd, timeout)

    transport = FakeTransport(handler=py_handler)
    spec = _spec(required_python="3.10")
    res = run_guest_build(spec, transport)
    assert res.status == STATUS_INFRASTRUCTURE_FAILURE
    assert res.reason_code == "PYTHON_VERSION_MISMATCH"
    assert "task requires Python '3.10'" in res.stderr_tail
    assert "guest provides '3.13.0'" in res.stderr_tail


def test_preflight_build_environment_checks_python_version():
    """preflight_build_environment verifies required_python_version against report."""
    def _readiness(py_ver="3.13.0"):
        return {
            "schema": GUEST_READINESS_SCHEMA,
            "computer_name": "F2-S8-BUILD1",
            "user": "builder",
            "is_admin": False,
            "python": r"C:\Tools\Python313\python.exe",
            "python_version": py_ver,
            "git": r"C:\Program Files\Git\cmd\git.exe",
            "uv": r"C:\Tools\uv.exe",
            "uv_sha256": UV_APPROVED_SHA256,
            "free_bytes": 50_000_000_000,
        }

    # Matches
    preflight = preflight_build_environment(
        vm_name="F2-S8-BUILD1",
        network_policy="CONTROLLED_ACQUISITION_OFFLINE_EXECUTION",
        readiness_report=_readiness("3.10.12"),
        phase_disconnect_verified=True,
        required_python_version="3.10",
    )
    assert preflight.python_version == "3.10.12"

    # Mismatch raises
    with pytest.raises(BuildPrerequisiteError, match="does not satisfy '3.10'"):
        preflight_build_environment(
            vm_name="F2-S8-BUILD1",
            network_policy="CONTROLLED_ACQUISITION_OFFLINE_EXECUTION",
            readiness_report=_readiness("3.13.0"),
            phase_disconnect_verified=True,
            required_python_version="3.10",
        )


