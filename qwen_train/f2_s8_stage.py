"""F2-S8 guest-workspace staging: reproducible manifest, transfer, byte verification.

Why this module exists
----------------------
``f2_s8_pilot_build`` builds each task's workspace under the FIXED absolute root
``C:\\f2_pilot`` and documents that the root is *reproduced identically inside the
guest*, because a virtual environment embeds absolute paths in its launcher
scripts and console entry points (``f2_s8_pilot_build`` module docstring).  The
live runner, however, only ever staged ``spec.json``: ``per_task_env`` requires
the guest to already hold ``C:\\f2_pilot\\<task>\\repo``, ``...\\repo_gold`` and
the per-side environment (``.venv`` / ``.venv_gold``, or ``repo\\.venv`` for the
uv-owned tasks).  Nothing in the repository produced or verified that placement,
so "the guest is an identical reproduction" was an assumption rather than a
checked contract.

This module makes it a checked contract.  For one task workspace it:

1. walks the SOURCE tree read-only and records every file's relative POSIX path,
   byte size and SHA-256 (generated caches are excluded explicitly and the
   exclusion policy is recorded inside the manifest);
2. refuses to stage a task whose guest directory already exists unless the caller
   explicitly allows it, so the pre-existing guest workspaces cannot be silently
   overwritten;
3. transfers the tree over the existing D5-approved PowerShell Direct channel;
4. has the GUEST measure its own bytes (``Get-FileHash``) and compares that
   listing to the manifest, so a truncated, partial, extra or altered transfer is
   detected rather than assumed away;
5. can roll the staging back with a command scoped to exactly the staged path.

Semantics that are deliberately fixed
-------------------------------------
* **Identity** is the *file set*: relative path + size + SHA-256.  Empty
  directories carry no evaluated content and are not part of the contract.
* **Exclusions** are limited to regenerable caches.  Removing ``__pycache__`` or
  ``.pytest_cache`` cannot change what a declared ``pytest`` command evaluates,
  because Python regenerates byte-code; nothing else is excluded by default.
* **Symlinks** are refused (``STAGE_UNSUPPORTED_SYMLINK``) rather than
  half-reproduced: the Windows guest has no faithful equivalent of a POSIX
  symlink and git checks them out as text when ``core.symlinks`` is false, so a
  source tree containing one needs an explicit, justified decision.
* **Executable bits and permissions** are NOT part of the contract.  Windows
  presents no POSIX executable bit, and the declared commands are invoked through
  the interpreter (``python -m pytest`` / ``uv run pytest``).  File *content* --
  including every ``Scripts\\*.exe`` launcher -- is hashed.
* **Path normalisation** is POSIX-relative (``/``) on both sides, so the check is
  independent of the drive or parent directory.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

__all__ = [
    "SCHEMA_VERSION",
    "DEFAULT_EXCLUDES",
    "STAGING_STATES",
    "STAGE_VERIFIED",
    "STAGE_MISSING",
    "STAGE_MALFORMED",
    "STAGE_UNKNOWN_SCHEMA",
    "STAGE_MISSING_FILE",
    "STAGE_EXTRA_FILE",
    "STAGE_CHANGED_BYTES",
    "STAGE_SIZE_MISMATCH",
    "STAGE_INCOMPLETE",
    "STAGE_UNSUPPORTED_SYMLINK",
    "STAGE_SOURCE_MISMATCH",
    "STAGE_PREEXISTING_CONFLICT",
    "STAGE_TRANSFER_FAILED",
    "StagingError",
    "StagedEntry",
    "StagingManifest",
    "StagingVerification",
    "build_staging_manifest",
    "load_manifest",
    "verify_tree",
    "parse_guest_listing",
    "verify_guest_listing",
    "plan_conflicts",
    "guest_verify_script",
    "rollback_script",
    "stage_workspace",
    "psdirect_staging_channel",
    "main",
]

SCHEMA_VERSION = "f2-s8-staging-manifest/v1"

#: Regenerable caches only.  ``*.pyc``/``*.pyo`` are byte-code; pytest, ruff and
#: mypy caches are derived.  Any of them can be absent without changing what a
#: declared ``pytest`` command evaluates.
DEFAULT_EXCLUDES: tuple[str, ...] = (
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "*.pyc",
    "*.pyo",
)

STAGE_VERIFIED = "STAGE_VERIFIED"
STAGE_MISSING = "STAGE_MISSING"
STAGE_MALFORMED = "STAGE_MALFORMED"
STAGE_UNKNOWN_SCHEMA = "STAGE_UNKNOWN_SCHEMA"
STAGE_MISSING_FILE = "STAGE_MISSING_FILE"
STAGE_EXTRA_FILE = "STAGE_EXTRA_FILE"
STAGE_CHANGED_BYTES = "STAGE_CHANGED_BYTES"
STAGE_SIZE_MISMATCH = "STAGE_SIZE_MISMATCH"
STAGE_INCOMPLETE = "STAGE_INCOMPLETE"
STAGE_UNSUPPORTED_SYMLINK = "STAGE_UNSUPPORTED_SYMLINK"
STAGE_SOURCE_MISMATCH = "STAGE_SOURCE_MISMATCH"
STAGE_PREEXISTING_CONFLICT = "STAGE_PREEXISTING_CONFLICT"
STAGE_TRANSFER_FAILED = "STAGE_TRANSFER_FAILED"

STAGING_STATES = (
    STAGE_VERIFIED,
    STAGE_MISSING,
    STAGE_MALFORMED,
    STAGE_UNKNOWN_SCHEMA,
    STAGE_MISSING_FILE,
    STAGE_EXTRA_FILE,
    STAGE_CHANGED_BYTES,
    STAGE_SIZE_MISMATCH,
    STAGE_INCOMPLETE,
    STAGE_UNSUPPORTED_SYMLINK,
    STAGE_SOURCE_MISMATCH,
    STAGE_PREEXISTING_CONFLICT,
    STAGE_TRANSFER_FAILED,
)

_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


class StagingError(RuntimeError):
    """Staging could not proceed (fail closed)."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_sha256(value: Any) -> bool:
    s = str(value or "")
    return len(s) == 64 and all(c in "0123456789abcdef" for c in s)


def _canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _excluded(rel_posix: str, patterns: Sequence[str]) -> bool:
    """True when any component or basename matches a pattern.

    A pattern containing ``/`` is matched against the whole relative path; a
    pattern without one matches a single path component or the basename.
    """
    parts = rel_posix.split("/")
    for pattern in patterns:
        if "/" in pattern:
            if rel_posix == pattern.rstrip("/"):
                return True
            continue
        for part in parts:
            if part == pattern:
                return True
            if pattern.startswith("*.") and part.endswith(pattern[1:]):
                return True
            if pattern == "*":
                return True
    return False


def _hash_file_exact(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class StagedEntry:
    path: str
    size: int
    digest: str

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "size": int(self.size), "digest": self.digest}


@dataclass(frozen=True)
class StagingVerification:
    """Result of one staging check.  Only ``STAGE_VERIFIED`` is accepting."""

    state: str
    detail: str = ""
    missing: tuple[str, ...] = ()
    extra: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.state == STAGE_VERIFIED

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "detail": self.detail,
            "missing": list(self.missing),
            "extra": list(self.extra),
            "changed": list(self.changed),
        }


@dataclass(frozen=True)
class StagingManifest:
    """The byte-level contract for one staged workspace."""

    schema_version: str
    task_id: str
    source_root: str
    guest_root: str
    exclude_patterns: tuple[str, ...]
    entries: tuple[StagedEntry, ...]
    file_count: int
    total_bytes: int
    manifest_digest: str = ""
    excluded: tuple[str, ...] = field(default=(), compare=False)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "source_root": self.source_root,
            "guest_root": self.guest_root,
            "exclude_patterns": list(self.exclude_patterns),
            "file_count": int(self.file_count),
            "total_bytes": int(self.total_bytes),
            "entries": [e.to_dict() for e in self.entries],
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.canonical_payload())

    def compute_digest(self) -> str:
        return _sha256(self.canonical_bytes())

    def bind(self) -> "StagingManifest":
        return StagingManifest(
            **{**self.__dict__, "manifest_digest": self.compute_digest()}
        )

    def to_dict(self) -> dict[str, Any]:
        d = self.canonical_payload()
        d["manifest_digest"] = self.manifest_digest
        d["excluded"] = list(self.excluded)
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "StagingManifest":
        raw_entries = d.get("entries")
        if not isinstance(raw_entries, list):
            raise ValueError("entries must be a list")
        entries = []
        for item in raw_entries:
            if not isinstance(item, Mapping):
                raise ValueError("each entry must be an object")
            entries.append(
                StagedEntry(
                    path=str(item.get("path") or ""),
                    size=int(item.get("size") or 0),
                    digest=str(item.get("digest") or ""),
                )
            )
        excluded = d.get("excluded") or []
        return cls(
            schema_version=str(d.get("schema_version") or ""),
            task_id=str(d.get("task_id") or ""),
            source_root=str(d.get("source_root") or ""),
            guest_root=str(d.get("guest_root") or ""),
            exclude_patterns=tuple(str(p) for p in (d.get("exclude_patterns") or [])),
            entries=tuple(entries),
            file_count=int(d.get("file_count") or 0),
            total_bytes=int(d.get("total_bytes") or 0),
            manifest_digest=str(d.get("manifest_digest") or ""),
            excluded=tuple(str(p) for p in excluded),
        )


def _walk_files(
    root: Path, patterns: Sequence[str]
) -> tuple[list[StagedEntry], tuple[str, ...]]:
    entries: list[StagedEntry] = []
    excluded: list[str] = []
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                children = sorted(it, key=lambda e: e.name)
        except OSError as exc:
            raise StagingError(f"unreadable directory {current}: {exc}") from exc
        for entry in children:
            path = Path(entry.path)
            rel = path.relative_to(root).as_posix()
            if entry.is_symlink():
                raise StagingError(
                    f"{rel}: symlink found in the source workspace; a POSIX symlink "
                    "has no faithful Windows-guest equivalent and is refused rather "
                    "than half-reproduced (STAGE_UNSUPPORTED_SYMLINK)"
                )
            if entry.is_dir(follow_symlinks=False):
                if _excluded(rel, patterns):
                    excluded.append(rel)
                    continue
                stack.append(path)
                continue
            if _excluded(rel, patterns):
                excluded.append(rel)
                continue
            try:
                digest, size = _hash_file_exact(path)
            except OSError as exc:
                raise StagingError(f"unreadable file {rel}: {exc}") from exc
            entries.append(StagedEntry(path=rel, size=size, digest=digest))
    entries.sort(key=lambda e: e.path)
    return entries, tuple(sorted(excluded))


def build_staging_manifest(
    *,
    task_id: str,
    source_root: Path | str,
    guest_root: Path | str,
    excludes: Sequence[str] | None = None,
) -> StagingManifest:
    """Measure a host workspace read-only into a bound staging manifest.

    ``source_root`` is the HOST path (``C:\\f2_pilot\\<task_id>``) and
    ``guest_root`` the guest path it must reproduce.  The manifest is the exact
    contract the guest is checked against; it never mutates the source tree.
    """
    if not _SAFE_NAME.match(str(task_id)):
        raise StagingError(f"unsafe task_id {task_id!r}")
    src = Path(source_root)
    if not src.is_dir():
        raise StagingError(f"source workspace is not a directory: {src}")
    patterns = tuple(DEFAULT_EXCLUDES if excludes is None else excludes)
    entries, excluded = _walk_files(src, patterns)
    if not entries:
        raise StagingError(f"source workspace {src} contains no files to stage")
    # Canonicalise BOTH roots through Path() so the manifest digest is a
    # function of the staging CONTRACT, not of how the caller happened to write
    # the separator.  ``C:/f2_pilot/x`` and ``C:\\f2_pilot\\x`` describe the same
    # destination and must bind the same digest; otherwise a caller could
    # re-describe an identical transfer under a different digest.
    manifest = StagingManifest(
        schema_version=SCHEMA_VERSION,
        task_id=str(task_id),
        source_root=str(src),
        guest_root=str(Path(guest_root)),
        exclude_patterns=patterns,
        entries=tuple(entries),
        file_count=len(entries),
        total_bytes=sum(e.size for e in entries),
        excluded=excluded,
    )
    return manifest.bind()


def load_manifest(path: Path | str) -> StagingManifest:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - normalise to a reason
        raise StagingError(f"manifest {path} could not be read: {exc}") from exc
    try:
        return StagingManifest.from_dict(payload)
    except Exception as exc:  # noqa: BLE001
        raise StagingError(f"manifest {path} is malformed: {exc}") from exc


# ---------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------
def _check_header(manifest: StagingManifest) -> StagingVerification | None:
    if not isinstance(manifest, StagingManifest):
        return StagingVerification(STAGE_MALFORMED, "unsupported manifest type")
    if manifest.schema_version != SCHEMA_VERSION:
        return StagingVerification(
            STAGE_UNKNOWN_SCHEMA,
            f"unknown schema_version {manifest.schema_version!r} "
            f"(expected {SCHEMA_VERSION!r})",
        )
    if not manifest.task_id or not manifest.guest_root:
        return StagingVerification(
            STAGE_MALFORMED, "task_id and guest_root are required"
        )
    if manifest.file_count != len(manifest.entries):
        return StagingVerification(
            STAGE_MALFORMED, "file_count disagrees with the entry list"
        )
    if not manifest.manifest_digest or manifest.manifest_digest != manifest.compute_digest():
        return StagingVerification(
            STAGE_MALFORMED, "manifest_digest does not match the manifest content"
        )
    for entry in manifest.entries:
        if not entry.path or not _is_sha256(entry.digest):
            return StagingVerification(
                STAGE_MALFORMED, f"entry {entry.path!r} is not a valid file entry"
            )
    return None


def verify_tree(
    manifest: StagingManifest, root: Path | str
) -> StagingVerification:
    """Verify that ``root`` holds exactly the bytes the manifest binds."""
    bad = _check_header(manifest)
    if bad:
        return bad
    target = Path(root)
    if not target.is_dir():
        return StagingVerification(
            STAGE_MISSING, f"staged root does not exist: {target}"
        )
    try:
        actual_entries, _excluded = _walk_files(target, manifest.exclude_patterns)
    except StagingError as exc:
        return StagingVerification(STAGE_MISSING, str(exc))
    actual = {e.path: e for e in actual_entries}
    expected = {e.path: e for e in manifest.entries}

    missing = tuple(sorted(set(expected) - set(actual)))
    extra = tuple(sorted(set(actual) - set(expected)))
    changed: list[str] = []
    for path in sorted(set(expected) & set(actual)):
        want, got = expected[path], actual[path]
        if want.digest != got.digest:
            changed.append(path)
        elif want.size != got.size:
            changed.append(path)

    if missing:
        return StagingVerification(
            STAGE_MISSING_FILE,
            f"{len(missing)} file(s) missing from the staged tree",
            missing=missing,
        )
    if extra:
        return StagingVerification(
            STAGE_EXTRA_FILE,
            f"{len(extra)} unexpected file(s) in the staged tree",
            extra=extra,
        )
    if changed:
        return StagingVerification(
            STAGE_CHANGED_BYTES,
            f"{len(changed)} file(s) differ from the manifest",
            changed=tuple(changed),
        )
    return StagingVerification(
        STAGE_VERIFIED,
        f"{len(expected)} file(s) match the manifest exactly",
    )


def parse_guest_listing(text: str) -> dict[str, tuple[int, str]]:
    """Parse the guest's ``relpath<TAB>size<TAB>sha256`` listing, fail-closed."""
    if not str(text or "").strip():
        raise StagingError("guest listing is empty")
    rows: dict[str, tuple[int, str]] = {}
    for lineno, raw in enumerate(str(text).splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            raise StagingError(f"guest listing line {lineno} is malformed: {raw!r}")
        rel, size_s, digest = parts
        try:
            size = int(size_s)
        except ValueError as exc:
            raise StagingError(
                f"guest listing line {lineno} has a non-integer size: {size_s!r}"
            ) from exc
        digest = digest.lower()
        if not _is_sha256(digest):
            raise StagingError(
                f"guest listing line {lineno} has a non-SHA-256 digest: {digest!r}"
            )
        if rel in rows:
            raise StagingError(f"guest listing names {rel!r} twice")
        rows[rel] = (size, digest)
    return rows


def verify_guest_listing(
    manifest: StagingManifest, listing: str | Mapping[str, tuple[int, str]]
) -> StagingVerification:
    """Compare the GUEST's own byte measurement against the manifest."""
    bad = _check_header(manifest)
    if bad:
        return bad
    try:
        rows = (
            parse_guest_listing(listing)
            if isinstance(listing, str)
            else dict(listing)
        )
    except StagingError as exc:
        return StagingVerification(STAGE_INCOMPLETE, str(exc))

    expected = {e.path: e for e in manifest.entries}
    missing = tuple(sorted(set(expected) - set(rows)))
    extra = tuple(sorted(set(rows) - set(expected)))
    changed: list[str] = []
    size_mismatch: list[str] = []
    for path in sorted(set(expected) & set(rows)):
        want, (got_size, got_digest) = expected[path], rows[path]
        if want.digest != got_digest:
            changed.append(path)
        elif want.size != got_size:
            size_mismatch.append(path)

    if missing:
        return StagingVerification(
            STAGE_MISSING_FILE,
            f"{len(missing)} file(s) absent in the guest after transfer",
            missing=missing,
        )
    if changed:
        return StagingVerification(
            STAGE_CHANGED_BYTES,
            f"{len(changed)} file(s) differ between host and guest",
            changed=tuple(changed),
        )
    if size_mismatch:
        return StagingVerification(
            STAGE_SIZE_MISMATCH,
            f"{len(size_mismatch)} file(s) have the right bytes but the wrong size",
            changed=tuple(size_mismatch),
        )
    if extra:
        # Extra files are reported, not ignored: an unaccounted file in the guest
        # workspace could shadow a module the declared command imports.
        return StagingVerification(
            STAGE_EXTRA_FILE,
            f"{len(extra)} unexpected file(s) present in the guest workspace",
            extra=extra,
        )
    return StagingVerification(
        STAGE_VERIFIED,
        f"{len(expected)} file(s) verified byte-for-byte in the guest",
    )


def plan_conflicts(
    existing_names: Iterable[str],
    task_ids: Iterable[str],
    *,
    allow_existing: bool = False,
) -> tuple[str, ...]:
    """Task ids whose guest directory already exists.

    Staging never overwrites a pre-existing workspace.  The caller must either
    select tasks that do not collide, or pass ``allow_existing=True`` under an
    explicit, independently justified plan.
    """
    if allow_existing:
        return ()
    present = {str(n) for n in existing_names}
    return tuple(sorted(t for t in task_ids if str(t) in present))


# ---------------------------------------------------------------------------
# guest-side command generation
# ---------------------------------------------------------------------------
def _ps_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def guest_verify_script(manifest: StagingManifest, *, listing_path: str) -> str:
    """PowerShell that makes the GUEST measure its own staged bytes.

    Emits ``relpath<TAB>size<TAB>sha256`` for every *contract* file under the
    guest root.  The guest therefore reports bytes rather than being told what it
    should say.

    The manifest's exclusion policy is applied on BOTH sides.  The transfer copies
    the whole directory (so an excluded cache is physically present in the guest,
    exactly as it is on the host), and both the host manifest and the guest listing
    skip the same paths -- otherwise the guest's regenerable ``__pycache__`` would
    be reported as an "extra" file on every run.  The matcher below mirrors
    :func:`_excluded` exactly: a pattern without ``/`` matches one path component
    or a ``*.ext`` suffix; a pattern with ``/`` matches the whole relative path.
    """
    root = _ps_quote(manifest.guest_root)
    listing = _ps_quote(listing_path)
    patterns = ", ".join(_ps_quote(p) for p in manifest.exclude_patterns)
    return (
        "$ErrorActionPreference='Stop'; "
        f"$root = {root}; $out = {listing}; "
        f"$patterns = @({patterns}); "
        "if (-not (Test-Path -LiteralPath $root -PathType Container)) "
        "{ throw \"staged root missing: $root\" }; "
        "function Test-Excluded([string]$rel) { "
        "$parts = $rel -split '/'; "
        "foreach ($p in $patterns) { "
        "if ($p.Contains('/')) { if ($rel -eq $p) { return $true }; continue }; "
        "foreach ($part in $parts) { "
        "if ($part -eq $p) { return $true }; "
        "if ($p.StartsWith('*.') -and $part.EndsWith($p.Substring(1))) "
        "{ return $true }; "
        "if ($p -eq '*') { return $true } } } "
        "return $false }; "
        "$rows = New-Object System.Collections.Generic.List[string]; "
        "Get-ChildItem -LiteralPath $root -Recurse -Force -File | ForEach-Object { "
        "$full = $_.FullName; "
        "$rel = $full.Substring($root.Length).TrimStart('\\','/').Replace('\\','/'); "
        "if (-not (Test-Excluded $rel)) { "
        "$h = (Get-FileHash -LiteralPath $full -Algorithm SHA256).Hash.ToLower(); "
        "$rows.Add($rel + \"`t\" + $_.Length + \"`t\" + $h) } }; "
        "Set-Content -LiteralPath $out -Value $rows -Encoding UTF8"
    )


def rollback_script(manifest: StagingManifest) -> str:
    """Remove EXACTLY the staged workspace.  Never a wildcard, never a parent."""
    return (
        "$ErrorActionPreference='Stop'; "
        f"$target = {_ps_quote(manifest.guest_root)}; "
        "if ($target -notmatch '^[A-Za-z]:\\\\[^\\\\]+$' -and "
        "$target.Split('\\').Count -lt 3) "
        "{ throw \"refusing to remove a top-level path: $target\" }; "
        "Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction SilentlyContinue"
    )


def default_listing_path(manifest: StagingManifest) -> str:
    """The verification listing lives OUTSIDE the staged tree, always.

    A listing written inside ``guest_root`` would be an unaccounted file in the
    very workspace the declared ``pytest`` command runs in -- pytest would collect
    it, and every later verification would see it as an extra file.
    """
    parent = str(Path(manifest.guest_root).parent).replace("/", "\\")
    return f"{parent}\\{manifest.task_id}.staging_listing.txt"


def listing_cleanup_script(listing_path: str) -> str:
    return (
        "$ErrorActionPreference='Stop'; "
        f"Remove-Item -LiteralPath {_ps_quote(listing_path)} -Force "
        "-ErrorAction SilentlyContinue"
    )


# ---------------------------------------------------------------------------
# channel + orchestration
# ---------------------------------------------------------------------------
class StagingChannel:
    """The two channel operations staging needs (injectable for tests)."""

    def __init__(self, *, vm_name: str = "F2-S8-VM1", credential_path: str = "",
                 pwsh: str = "pwsh") -> None:
        self.vm_name = vm_name
        self.credential_path = credential_path
        self.pwsh = pwsh

    # pragma: no cover - exercised only against a real guest
    def put_tree(self, local: Path | str, remote_parent: str) -> None:
        import subprocess

        script = (
            "$ErrorActionPreference='Stop'; "
            f"$cred = Import-Clixml -LiteralPath {_ps_quote(self.credential_path)}; "
            f"$s = New-PSSession -VMName {_ps_quote(self.vm_name)} -Credential $cred; "
            "try { "
            f"Copy-Item -ToSession $s -LiteralPath {_ps_quote(str(local))} "
            f"-Destination {_ps_quote(remote_parent)} -Recurse -Force "
            "} finally { Remove-PSSession -Session $s; $cred=$null }"
        )
        proc = subprocess.run(
            [self.pwsh, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            timeout=14400,
        )
        if proc.returncode != 0:
            raise StagingError(
                f"transfer failed: {proc.stderr.decode(errors='replace').strip()[:500]}"
            )

    # pragma: no cover - exercised only against a real guest
    def run_guest(self, script: str, *, timeout: int = 7200) -> str:
        import subprocess

        wrapped = (
            "$ErrorActionPreference='Stop'; "
            f"$cred = Import-Clixml -LiteralPath {_ps_quote(self.credential_path)}; "
            f"$s = New-PSSession -VMName {_ps_quote(self.vm_name)} -Credential $cred; "
            f"try {{ Invoke-Command -Session $s -ScriptBlock {{ {script} }} }} "
            "finally { Remove-PSSession -Session $s; $cred=$null }"
        )
        proc = subprocess.run(
            [self.pwsh, "-NoProfile", "-NonInteractive", "-Command", wrapped],
            capture_output=True,
            timeout=timeout,
        )
        if proc.returncode != 0:
            raise StagingError(
                f"guest command failed: {proc.stderr.decode(errors='replace').strip()[:500]}"
            )
        return proc.stdout.decode(errors="replace")

    def get_text(self, remote: str) -> str:
        import tempfile
        import subprocess

        with tempfile.TemporaryDirectory() as td:
            local = Path(td) / "guest-listing.txt"
            script = (
                "$ErrorActionPreference='Stop'; "
                f"$cred = Import-Clixml -LiteralPath {_ps_quote(self.credential_path)}; "
                f"$s = New-PSSession -VMName {_ps_quote(self.vm_name)} -Credential $cred; "
                "try { "
                f"Copy-Item -FromSession $s -LiteralPath {_ps_quote(remote)} "
                f"-Destination {_ps_quote(str(local))} -Force "
                "} finally { Remove-PSSession -Session $s; $cred=$null }"
            )
            proc = subprocess.run(
                [self.pwsh, "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                timeout=600,
            )
            if proc.returncode != 0:
                raise StagingError(
                    f"fetch failed: {proc.stderr.decode(errors='replace').strip()[:500]}"
                )
            return local.read_text(encoding="utf-8-sig")


def psdirect_staging_channel(
    *, vm_name: str = "F2-S8-VM1", credential_path: str, pwsh: str = "pwsh"
) -> StagingChannel:
    return StagingChannel(vm_name=vm_name, credential_path=credential_path, pwsh=pwsh)


def stage_workspace(
    *,
    manifest: StagingManifest,
    host_root: Path | str,
    channel: Any,
    existing_names: Sequence[str] = (),
    allow_existing: bool = False,
    listing_path: str = "",
    progress: Callable[[str], None] | None = None,
) -> StagingVerification:
    """Stage one workspace and verify it byte-for-byte in the guest.

    Ordering is fail-closed: the HOST tree is verified against the manifest
    before anything is transferred (so the transfer cannot carry bytes that were
    never hashed), a pre-existing guest directory refuses the stage, and the
    guest's own listing decides the outcome.  Nothing here touches the
    authorization grant.
    """
    say = progress or (lambda _m: None)
    bad = _check_header(manifest)
    if bad:
        return bad

    conflicts = plan_conflicts(existing_names, [manifest.task_id], allow_existing=allow_existing)
    if conflicts:
        return StagingVerification(
            STAGE_PREEXISTING_CONFLICT,
            f"guest workspace {manifest.guest_root!r} already exists; refusing to "
            "overwrite it without an explicitly justified plan",
        )

    local = verify_tree(manifest, host_root)
    if not local.ok:
        return StagingVerification(
            STAGE_SOURCE_MISMATCH,
            f"host workspace does not match the manifest: {local.detail}",
            missing=local.missing,
            extra=local.extra,
            changed=local.changed,
        )
    say(f"{manifest.task_id}: host tree verified ({manifest.file_count} files)")

    try:
        channel.put_tree(Path(manifest.source_root), str(Path(manifest.guest_root).parent))
    except Exception as exc:  # noqa: BLE001 - channel failures normalise
        return StagingVerification(STAGE_TRANSFER_FAILED, f"transfer failed: {exc}")
    say(f"{manifest.task_id}: transferred to {manifest.guest_root}")

    listing = listing_path or default_listing_path(manifest)
    try:
        channel.run_guest(guest_verify_script(manifest, listing_path=listing))
        text = channel.get_text(listing)
    except Exception as exc:  # noqa: BLE001
        return StagingVerification(
            STAGE_TRANSFER_FAILED, f"guest verification failed: {exc}"
        )
    finally:
        try:
            channel.run_guest(listing_cleanup_script(listing))
        except Exception:  # noqa: BLE001 - cleanup must never mask the verdict
            pass
    verdict = verify_guest_listing(manifest, text)
    say(f"{manifest.task_id}: guest verification -> {verdict.state}")
    return verdict


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _batch_manifests(args: Any) -> int:
    """Measure every built workspace of a Stage-1 manifest, read-only.

    This is the reproducible staging-manifest procedure for a whole candidate
    set: one manifest per built task, each bound to its own digest, plus a
    top-level digest over the set so a later transfer can be checked against an
    immutable description of *what was supposed to move*.  ``build_failure``
    entries are skipped (there is no workspace to stage) and the skip is
    recorded rather than silent.
    """
    import hashlib

    src = Path(args.build_manifest)
    build_bytes = src.read_bytes()
    build_digest = hashlib.sha256(build_bytes).hexdigest()
    build = json.loads(build_bytes.decode("utf-8"))

    only = set(args.only or [])
    results: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []

    for task_id in sorted(build):
        entry = build[task_id] or {}
        if only and task_id not in only:
            continue
        status = str(entry.get("status") or "")
        if status != "built":
            skipped.append({"task_id": task_id, "status": status or "unknown"})
            continue
        if not args.quiet:
            print(f"  staging-manifest {task_id}", file=sys.stderr)
        try:
            manifest = build_staging_manifest(
                task_id=task_id,
                source_root=entry["workspace"],
                guest_root=str(Path(args.guest_root) / task_id),
            )
        except (StagingError, KeyError) as exc:
            failures.append({"task_id": task_id, "detail": str(exc)})
            continue
        results.append(
            {
                "task_id": task_id,
                "source_root": manifest.source_root,
                "guest_root": manifest.guest_root,
                "file_count": manifest.file_count,
                "total_bytes": manifest.total_bytes,
                "manifest_digest": manifest.manifest_digest,
            }
        )

    payload = {
        "schema_version": SCHEMA_VERSION,
        "build_manifest": str(src),
        "build_manifest_digest": build_digest,
        "guest_root": args.guest_root,
        "staged_count": len(results),
        "skipped_count": len(skipped),
        "failed_count": len(failures),
        "workspaces": results,
        "skipped": skipped,
        "failures": failures,
    }
    payload["set_digest"] = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    Path(args.out).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        f"wrote {len(results)} staging manifest(s), {len(skipped)} skipped, "
        f"{len(failures)} failure(s) -> {args.out}",
        file=sys.stderr,
    )
    return 0 if not failures else 1


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m qwen_train.f2_s8_stage")
    sub = parser.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("manifest", help="measure a host workspace into a manifest")
    m.add_argument("--task-id", required=True)
    m.add_argument("--source-root", required=True)
    m.add_argument("--guest-root", required=True)
    m.add_argument("--out", required=True)
    m.add_argument("--exclude", action="append", default=None)

    v = sub.add_parser("verify", help="verify a tree or a guest listing")
    v.add_argument("--manifest", required=True)
    v.add_argument("--root", default="", help="verify this tree against the manifest")
    v.add_argument("--listing", default="", help="verify a guest listing file")
    v.add_argument("--exclude-existing", nargs="*", default=None,
                   help="names already present in the guest workspace root")

    p = sub.add_parser("plan", help="print the guest-side commands for one task")
    p.add_argument("--manifest", required=True)
    p.add_argument("--listing", default="")

    b = sub.add_parser(
        "batch",
        help="measure every built task of a Stage-1 build manifest into staging manifests",
    )
    b.add_argument("--build-manifest", required=True,
                   help="the Stage-1 build_manifest.json (read-only)")
    b.add_argument("--guest-root", default="C:/f2_pilot",
                   help="guest root the workspaces must reproduce")
    b.add_argument("--only", action="append", default=[], help="limit to one task id (repeatable)")
    b.add_argument("--out", required=True, help="write the batch artifact JSON here")
    b.add_argument("--quiet", action="store_true")

    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.cmd == "batch":
        return _batch_manifests(args)

    if args.cmd == "manifest":
        manifest = build_staging_manifest(
            task_id=args.task_id,
            source_root=args.source_root,
            guest_root=args.guest_root,
            excludes=args.exclude,
        )
        Path(args.out).write_text(
            json.dumps(manifest.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(json.dumps({
            "task_id": manifest.task_id,
            "file_count": manifest.file_count,
            "total_bytes": manifest.total_bytes,
            "manifest_digest": manifest.manifest_digest,
        }, indent=2, sort_keys=True))
        return 0

    if args.cmd == "verify":
        manifest = load_manifest(args.manifest)
        if args.listing:
            verdict = verify_guest_listing(
                manifest, Path(args.listing).read_text(encoding="utf-8-sig")
            )
        elif args.root:
            verdict = verify_tree(manifest, args.root)
        else:
            raise SystemExit("REFUSING: supply --root or --listing")
        print(json.dumps(verdict.to_dict(), indent=2, sort_keys=True))
        return 0 if verdict.ok else 1

    manifest = load_manifest(args.manifest)
    listing = args.listing or default_listing_path(manifest)
    print("### guest verification")
    print(guest_verify_script(manifest, listing_path=listing))
    print("### listing cleanup (leaves no artifact inside the staged tree)")
    print(listing_cleanup_script(listing))
    print("### rollback (removes exactly the staged workspace)")
    print(rollback_script(manifest))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
