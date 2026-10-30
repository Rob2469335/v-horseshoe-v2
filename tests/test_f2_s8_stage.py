"""``f2-s8-staging-manifest/v1`` guest-workspace staging contract — tests.

Covers manifest creation, host-tree verification, guest-listing verification,
missing / extra / changed / truncated transfers, path handling, exclusion policy,
symlink refusal, pre-existing-workspace preservation, rollback scoping and the
CLI.  Nothing here contacts a guest, mutates ``C:\\f2_pilot`` or consumes any
authorization.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path, PureWindowsPath

import pytest

from qwen_train import f2_s8_stage as st


def _workspace(root: Path, *, tag: str = "base") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "repo").mkdir(exist_ok=True)
    (root / "repo" / "mod.py").write_text(f"X = {tag!r}\n", encoding="utf-8")
    (root / "repo_gold").mkdir(exist_ok=True)
    (root / "repo_gold" / "mod.py").write_text(f"X = {tag!r}\n# gold\n", encoding="utf-8")
    (root / ".venv" / "Scripts").mkdir(parents=True, exist_ok=True)
    (root / ".venv" / "Scripts" / "python.exe").write_bytes(b"MZ\x00fake")
    (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    return root


def _manifest(tmp_path: Path, task_id: str = "acme__repo-1") -> st.StagingManifest:
    host = _workspace(tmp_path / "host" / task_id)
    return st.build_staging_manifest(
        task_id=task_id,
        source_root=host,
        guest_root=str(tmp_path / "guest" / task_id),
    )


# --------------------------------------------------------------------------
# manifest
# --------------------------------------------------------------------------
def test_manifest_is_deterministic_and_content_bound(tmp_path: Path) -> None:
    task = "acme__repo-1"
    host = _workspace(tmp_path / "host" / task)
    first = st.build_staging_manifest(
        task_id=task, source_root=host, guest_root=str(tmp_path / "guest" / task)
    )
    second = st.build_staging_manifest(
        task_id=task, source_root=host, guest_root=str(tmp_path / "guest" / task)
    )
    assert first.manifest_digest == second.manifest_digest
    assert first.file_count == 4
    assert first.manifest_digest == first.compute_digest()

    (host / "repo" / "mod.py").write_text("X = 'changed'\n", encoding="utf-8")
    third = st.build_staging_manifest(
        task_id=task, source_root=host, guest_root=str(tmp_path / "guest" / task)
    )
    assert third.manifest_digest != first.manifest_digest


def test_generated_caches_are_excluded_and_recorded(tmp_path: Path) -> None:
    task = "acme__repo-1"
    host = _workspace(tmp_path / "host" / task)
    clean = st.build_staging_manifest(
        task_id=task, source_root=host, guest_root=str(tmp_path / "guest" / task)
    )
    (host / "repo" / "__pycache__").mkdir()
    (host / "repo" / "__pycache__" / "mod.cpython-314.pyc").write_bytes(b"pyc")
    (host / ".pytest_cache").mkdir()
    (host / ".pytest_cache" / "CACHEDIR.TAG").write_text("x", encoding="utf-8")
    after = st.build_staging_manifest(
        task_id=task, source_root=host, guest_root=str(tmp_path / "guest" / task)
    )
    assert after.manifest_digest == clean.manifest_digest
    assert after.file_count == clean.file_count
    assert "repo/__pycache__" in after.excluded
    assert ".pytest_cache" in after.excluded
    assert after.exclude_patterns == st.DEFAULT_EXCLUDES


def test_manifest_refuses_symlinks_and_bad_inputs(tmp_path: Path) -> None:
    task = "acme__repo-1"
    host = _workspace(tmp_path / "host" / task)
    try:
        os.symlink(host / "repo" / "mod.py", host / "repo" / "link.py")
    except (OSError, NotImplementedError):
        pass
    else:
        with pytest.raises(st.StagingError, match="symlink"):
            st.build_staging_manifest(
                task_id=task, source_root=host, guest_root=str(tmp_path / "g" / task)
            )
        os.remove(host / "repo" / "link.py")

    with pytest.raises(st.StagingError):
        st.build_staging_manifest(
            task_id="../escape", source_root=host, guest_root="C:/f2_pilot/x"
        )
    with pytest.raises(st.StagingError):
        st.build_staging_manifest(
            task_id=task, source_root=tmp_path / "nope", guest_root="C:/f2_pilot/x"
        )
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(st.StagingError):
        st.build_staging_manifest(
            task_id=task, source_root=empty, guest_root="C:/f2_pilot/x"
        )


def test_manifest_round_trips_through_json(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    payload = json.loads(json.dumps(manifest.to_dict()))
    restored = st.StagingManifest.from_dict(payload)
    assert restored.canonical_bytes() == manifest.canonical_bytes()
    assert st.verify_tree(restored, manifest.source_root).ok


# --------------------------------------------------------------------------
# host-tree verification
# --------------------------------------------------------------------------
def test_verify_tree_detects_missing_changed_and_extra(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    host = Path(manifest.source_root)
    assert st.verify_tree(manifest, host).state == st.STAGE_VERIFIED

    original = (host / "repo" / "mod.py").read_text(encoding="utf-8")
    (host / "repo" / "mod.py").unlink()
    v = st.verify_tree(manifest, host)
    assert v.state == st.STAGE_MISSING_FILE and v.missing == ("repo/mod.py",)

    (host / "repo" / "mod.py").write_text("tampered\n", encoding="utf-8")
    v = st.verify_tree(manifest, host)
    assert v.state == st.STAGE_CHANGED_BYTES and "repo/mod.py" in v.changed
    (host / "repo" / "mod.py").write_text(original, encoding="utf-8")

    (host / "repo" / "sneaky.py").write_text("x", encoding="utf-8")
    assert st.verify_tree(manifest, host).state == st.STAGE_EXTRA_FILE
    (host / "repo" / "sneaky.py").unlink()

    assert st.verify_tree(manifest, tmp_path / "absent").state == st.STAGE_MISSING


def test_verify_tree_rejects_a_tampered_or_unknown_manifest(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    tampered = st.StagingManifest.from_dict(
        {**manifest.to_dict(), "file_count": manifest.file_count + 1}
    )
    assert st.verify_tree(tampered, manifest.source_root).state == st.STAGE_MALFORMED

    # An entry whose digest is edited is caught by the manifest's own binding.
    payload = manifest.to_dict()
    payload["entries"][0]["digest"] = "0" * 64
    assert st.verify_tree(
        st.StagingManifest.from_dict(payload), manifest.source_root
    ).state == st.STAGE_MALFORMED

    unknown = st.StagingManifest.from_dict(
        {**manifest.to_dict(), "schema_version": "f2-s8-staging-manifest/v9"}
    )
    assert st.verify_tree(unknown, manifest.source_root).state == st.STAGE_UNKNOWN_SCHEMA

    assert st.verify_tree("not-a-manifest", manifest.source_root).state == st.STAGE_MALFORMED  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# guest listing
# --------------------------------------------------------------------------
def test_parse_guest_listing_is_fail_closed() -> None:
    good = "repo/mod.py\t8\t" + "a" * 64 + "\n.venv/Scripts/python.exe\t6\t" + "b" * 64
    rows = st.parse_guest_listing(good)
    assert rows["repo/mod.py"] == (8, "a" * 64)

    with pytest.raises(st.StagingError):
        st.parse_guest_listing("")
    with pytest.raises(st.StagingError):
        st.parse_guest_listing("only-one-column")
    with pytest.raises(st.StagingError):
        st.parse_guest_listing("repo/mod.py\tnotanint\t" + "a" * 64)
    with pytest.raises(st.StagingError):
        st.parse_guest_listing("repo/mod.py\t8\tdeadbeef")
    with pytest.raises(st.StagingError):
        st.parse_guest_listing(
            "repo/mod.py\t8\t" + "a" * 64 + "\nrepo/mod.py\t8\t" + "a" * 64
        )


def test_verify_guest_listing_outcomes(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    rows = {e.path: (e.size, e.digest) for e in manifest.entries}
    assert st.verify_guest_listing(manifest, rows).state == st.STAGE_VERIFIED

    missing = dict(rows)
    missing.pop("repo/mod.py")
    v = st.verify_guest_listing(manifest, missing)
    assert v.state == st.STAGE_MISSING_FILE and v.missing == ("repo/mod.py",)

    changed = dict(rows)
    changed["repo/mod.py"] = (8, "c" * 64)
    v = st.verify_guest_listing(manifest, changed)
    assert v.state == st.STAGE_CHANGED_BYTES and "repo/mod.py" in v.changed

    resized = dict(rows)
    resized["repo/mod.py"] = (999, rows["repo/mod.py"][1])
    assert st.verify_guest_listing(manifest, resized).state == st.STAGE_SIZE_MISMATCH

    extra = dict(rows)
    extra["repo/undeclared.py"] = (1, "d" * 64)
    v = st.verify_guest_listing(manifest, extra)
    assert v.state == st.STAGE_EXTRA_FILE and v.extra == ("repo/undeclared.py",)

    assert st.verify_guest_listing(manifest, "garbage").state == st.STAGE_INCOMPLETE


# --------------------------------------------------------------------------
# preservation + scoping
# --------------------------------------------------------------------------
def test_preexisting_guest_workspaces_are_preserved(tmp_path: Path) -> None:
    existing = ["django-guardian-896", "django-guardian-899", "acme__repo-1"]
    # By default the collision is reported and staging refuses.
    assert st.plan_conflicts(existing, ["acme__repo-1", "acme__repo-2"]) == (
        "acme__repo-1",
    )
    assert st.plan_conflicts(existing, ["acme__repo-2"]) == ()
    # Only an explicit, independently justified plan may proceed.
    assert st.plan_conflicts(
        existing, ["acme__repo-1"], allow_existing=True
    ) == ()


def test_stage_workspace_refuses_to_overwrite(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    channel = _FakeChannel(tmp_path / "guest", manifest)
    verdict = st.stage_workspace(
        manifest=manifest,
        host_root=manifest.source_root,
        channel=channel,
        existing_names=[manifest.task_id],
    )
    assert verdict.state == st.STAGE_PREEXISTING_CONFLICT
    assert not (tmp_path / "guest" / manifest.task_id).exists()


def test_rollback_script_is_scoped_and_refuses_top_level(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    script = st.rollback_script(manifest)
    assert manifest.guest_root in script
    assert "Remove-Item" in script
    assert "*" not in script

    top = st.StagingManifest.from_dict(
        {**manifest.to_dict(), "guest_root": "C:\\"}
    ).bind()
    assert "refusing to remove a top-level path" in st.rollback_script(top)


def test_guest_verify_script_targets_only_the_staged_root(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    script = st.guest_verify_script(manifest, listing_path="C:\\f2_s8\\listing.txt")
    assert manifest.guest_root in script
    assert "Get-FileHash" in script
    assert "Remove-Item" not in script
    # The manifest's exclusion policy is applied on the guest side too, otherwise
    # the transferred __pycache__ is reported as an "extra" file on every run.
    for pattern in manifest.exclude_patterns:
        assert f"'{pattern}'" in script
    assert "Test-Excluded" in script


def test_default_listing_path_lives_outside_the_staged_tree(tmp_path: Path) -> None:
    """Regression: a listing inside the tree is an extra file pytest would collect."""
    manifest = _manifest(tmp_path)
    listing = st.default_listing_path(manifest)
    guest_root = PureWindowsPath(manifest.guest_root)
    listing_path = PureWindowsPath(listing)
    # A SIBLING of the staged root, not a child of it.
    assert listing_path.parent == guest_root.parent
    assert guest_root not in listing_path.parents
    assert listing_path.name.startswith(manifest.task_id)
    assert "Remove-Item" in st.listing_cleanup_script(listing)


def test_stage_workspace_leaves_no_listing_artifact(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    seen: list[str] = []

    class _RecordingChannel(_FakeChannel):
        def run_guest(self, script: str, *, timeout: int = 7200) -> str:
            seen.append(script)
            return ""

    channel = _RecordingChannel(tmp_path / "guest", manifest)
    verdict = st.stage_workspace(
        manifest=manifest, host_root=manifest.source_root, channel=channel
    )
    assert verdict.ok, verdict.detail
    listing = st.default_listing_path(manifest)
    assert any(listing in s and "Get-FileHash" in s for s in seen)
    assert any(listing in s and "Remove-Item" in s for s in seen)


# --------------------------------------------------------------------------
# end-to-end transfer (fake guest; no VM, no network)
# --------------------------------------------------------------------------
class _FakeChannel:
    """A local stand-in for the PowerShell Direct channel.

    ``put_tree`` copies host -> local "guest" directory; ``get_text`` returns what
    the real guest script would emit, computed from the copied bytes.  Defects are
    injected to prove the verifier catches them rather than trusting the channel.
    """

    def __init__(
        self,
        guest_parent: Path,
        manifest: st.StagingManifest,
        *,
        skip: str = "",
        alter: str = "",
        add: str = "",
        fail: bool = False,
    ) -> None:
        self.guest_parent = Path(guest_parent)
        self.manifest = manifest
        self.skip = skip
        self.alter = alter
        self.add = add
        self.fail = fail
        self.transferred = False

    def put_tree(self, local: Path | str, remote_parent: str) -> None:
        if self.fail:
            raise st.StagingError("simulated channel failure")
        src = Path(local)
        dst = Path(remote_parent) / src.name
        shutil.copytree(src, dst)
        self.transferred = True

    def run_guest(self, script: str, *, timeout: int = 7200) -> str:
        return ""

    def get_text(self, remote: str) -> str:
        root = Path(self.manifest.guest_root)
        lines = []
        for entry in self.manifest.entries:
            if entry.path == self.skip:
                continue
            path = root / entry.path
            if not path.is_file():
                continue
            if entry.path == self.alter:
                path.write_text("altered-after-transfer\n", encoding="utf-8")
            data = path.read_bytes()
            import hashlib

            lines.append(
                f"{entry.path}\t{len(data)}\t{hashlib.sha256(data).hexdigest()}"
            )
        if self.add:
            extra = root / self.add
            extra.parent.mkdir(parents=True, exist_ok=True)
            data = b"unexpected"
            extra.write_bytes(data)
            import hashlib

            lines.append(
                f"{self.add}\t{len(data)}\t{hashlib.sha256(data).hexdigest()}"
            )
        return "\n".join(lines) + "\n"


def test_stage_workspace_verifies_a_clean_transfer(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    messages: list[str] = []
    channel = _FakeChannel(tmp_path / "guest", manifest)
    verdict = st.stage_workspace(
        manifest=manifest,
        host_root=manifest.source_root,
        channel=channel,
        progress=messages.append,
    )
    assert verdict.state == st.STAGE_VERIFIED, verdict.detail
    assert channel.transferred
    assert any("guest verification -> STAGE_VERIFIED" in m for m in messages)


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({"skip": "repo/mod.py"}, st.STAGE_MISSING_FILE),
        ({"alter": "repo/mod.py"}, st.STAGE_CHANGED_BYTES),
        ({"add": "repo/undeclared.py"}, st.STAGE_EXTRA_FILE),
        ({"fail": True}, st.STAGE_TRANSFER_FAILED),
    ],
)
def test_stage_workspace_fails_closed(
    tmp_path: Path, kwargs: dict, expected: str
) -> None:
    manifest = _manifest(tmp_path)
    channel = _FakeChannel(tmp_path / "guest", manifest, **kwargs)
    verdict = st.stage_workspace(
        manifest=manifest, host_root=manifest.source_root, channel=channel
    )
    assert verdict.state == expected, verdict.detail


def test_stage_workspace_rejects_a_host_tree_that_drifted(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    host = Path(manifest.source_root)
    (host / "repo" / "mod.py").write_text("drifted\n", encoding="utf-8")
    channel = _FakeChannel(tmp_path / "guest", manifest)
    verdict = st.stage_workspace(
        manifest=manifest, host_root=host, channel=channel
    )
    assert verdict.state == st.STAGE_SOURCE_MISMATCH
    # Nothing was transferred: the transfer never carried bytes that were not hashed.
    assert not channel.transferred


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def test_cli_manifest_then_verify(tmp_path: Path, capsys) -> None:
    task = "acme__repo-1"
    host = _workspace(tmp_path / "host" / task)
    out = tmp_path / "manifest.json"
    rc = st.main(
        [
            "manifest",
            "--task-id", task,
            "--source-root", str(host),
            "--guest-root", f"C:/f2_pilot/{task}",
            "--out", str(out),
        ]
    )
    assert rc == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["file_count"] == 4

    rc = st.main(["verify", "--manifest", str(out), "--root", str(host)])
    assert rc == 0
    assert "STAGE_VERIFIED" in capsys.readouterr().out

    (host / "repo" / "mod.py").write_text("bad\n", encoding="utf-8")
    rc = st.main(["verify", "--manifest", str(out), "--root", str(host)])
    assert rc == 1
    assert "STAGE_CHANGED_BYTES" in capsys.readouterr().out


def test_cli_plan_prints_guest_scripts(tmp_path: Path, capsys) -> None:
    manifest = _manifest(tmp_path)
    out = tmp_path / "m.json"
    out.write_text(json.dumps(manifest.to_dict()), encoding="utf-8")
    rc = st.main(["plan", "--manifest", str(out)])
    assert rc == 0
    text = capsys.readouterr().out
    assert "Get-FileHash" in text
    assert "### rollback" in text


# --------------------------------------------------------------------------
# canonicalisation of the staging contract
# --------------------------------------------------------------------------
def test_manifest_digest_is_invariant_to_path_separator_style(tmp_path: Path) -> None:
    """``C:/f2_pilot/x`` and ``C:\\f2_pilot\\x`` are the SAME destination.

    The digest must bind the staging contract, not the caller's spelling:
    otherwise an identical transfer could be re-described under a second digest,
    and the digest would stop being an immutable name for "what was supposed to
    move".
    """
    task = "acme__repo-1"
    host = _workspace(tmp_path / "host" / task)
    slashed = st.build_staging_manifest(
        task_id=task, source_root=str(host).replace("\\", "/"), guest_root=f"C:/f2_pilot/{task}"
    )
    backslashed = st.build_staging_manifest(
        task_id=task, source_root=str(host), guest_root=f"C:\\f2_pilot\\{task}"
    )
    assert slashed.entries == backslashed.entries
    assert slashed.manifest_digest == backslashed.manifest_digest
    assert slashed.guest_root == backslashed.guest_root == f"C:\\f2_pilot\\{task}"

    # the canonical form must round-trip through the CLI unchanged
    out = tmp_path / "m.json"
    assert st.main([
        "manifest", "--task-id", task, "--source-root", str(host),
        "--guest-root", f"C:/f2_pilot/{task}", "--out", str(out),
    ]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["guest_root"] == f"C:\\f2_pilot\\{task}"


# --------------------------------------------------------------------------
# batch staging manifests for a whole candidate set
# --------------------------------------------------------------------------
def _build_manifest_doc(tmp_path: Path) -> tuple[Path, list[str]]:
    """A Stage-1 shaped build manifest: built entries + build_failure entries."""
    doc: dict[str, dict] = {}
    built: list[str] = []
    for task in ("acme__repo-1", "acme__repo-2"):
        host = _workspace(tmp_path / "host" / task)
        doc[task] = {"status": "built", "workspace": str(host)}
        built.append(task)
    doc["acme__broken-3"] = {"status": "build_failure", "detail": "pip exploded"}
    doc["acme__skipped-4"] = {"status": "SKIP", "detail": "screened out"}
    path = tmp_path / "build_manifest.json"
    path.write_text(json.dumps(doc, indent=2, sort_keys=True), encoding="utf-8")
    return path, built


def test_batch_covers_built_and_records_skips(tmp_path: Path, capsys) -> None:
    build_doc, built = _build_manifest_doc(tmp_path)
    out = tmp_path / "batch.json"
    rc = st.main([
        "batch", "--build-manifest", str(build_doc),
        "--guest-root", "C:/f2_pilot", "--out", str(out),
    ])
    assert rc == 0
    batch = json.loads(out.read_text(encoding="utf-8"))
    assert batch["staged_count"] == len(built)
    assert batch["failed_count"] == 0
    assert {w["task_id"] for w in batch["workspaces"]} == set(built)
    # skips are recorded, never silent
    skipped = {s["task_id"]: s["status"] for s in batch["skipped"]}
    assert skipped == {"acme__broken-3": "build_failure", "acme__skipped-4": "SKIP"}
    # set_digest binds the whole description (everything except the digest itself)
    import hashlib

    recomputed = hashlib.sha256(
        st._canonical_bytes({k: v for k, v in batch.items() if k != "set_digest"})
    ).hexdigest()
    assert batch["set_digest"] == recomputed
    # guest roots are derived from the requested root and are canonical
    for workspace in batch["workspaces"]:
        assert workspace["guest_root"].startswith("C:\\f2_pilot\\")
        assert "\\" in workspace["guest_root"] and "/" not in workspace["guest_root"]
    capsys.readouterr()


def test_batch_manifests_are_stable_and_verifiable(tmp_path: Path) -> None:
    """A batch artifact must describe manifests that still verify against the tree."""
    build_doc, built = _build_manifest_doc(tmp_path)
    first = tmp_path / "a.json"
    second = tmp_path / "b.json"
    assert st.main(["batch", "--build-manifest", str(build_doc), "--out", str(first), "--quiet"]) == 0
    assert st.main(["batch", "--build-manifest", str(build_doc), "--out", str(second), "--quiet"]) == 0
    a = json.loads(first.read_text(encoding="utf-8"))
    b = json.loads(second.read_text(encoding="utf-8"))
    assert a["set_digest"] == b["set_digest"], "batch derivation must be deterministic"

    # every listed manifest digest must still verify against the live host tree
    for entry in a["workspaces"]:
        manifest = st.build_staging_manifest(
            task_id=entry["task_id"],
            source_root=entry["source_root"],
            guest_root=entry["guest_root"],
        )
        assert manifest.manifest_digest == entry["manifest_digest"]
        assert st.verify_tree(manifest, entry["source_root"]).ok


def test_batch_honours_only_and_refuses_nothing_silently(tmp_path: Path) -> None:
    build_doc, built = _build_manifest_doc(tmp_path)
    out = tmp_path / "batch.json"
    rc = st.main([
        "batch", "--build-manifest", str(build_doc),
        "--only", built[0], "--out", str(out), "--quiet",
    ])
    assert rc == 0
    batch = json.loads(out.read_text(encoding="utf-8"))
    assert [w["task_id"] for w in batch["workspaces"]] == [built[0]]
    # --only restricts the whole scan, so non-selected entries are neither
    # staged nor reported as skips; nothing outside the selection is touched.
    assert batch["staged_count"] == 1
    assert batch["skipped_count"] == 0
    assert batch["workspaces"][0]["task_id"] == built[0]
