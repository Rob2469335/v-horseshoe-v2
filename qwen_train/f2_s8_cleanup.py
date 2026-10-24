"""Fail-closed S8 workspace cleanup. Dry-run by default; explicit allow_delete only."""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import stat
import time
from dataclasses import dataclass
from typing import Iterable, Sequence

from qwen_train.f2_s8_factory import AppendOnlyLedger

TASK_DIR_RE = re.compile(r"^[A-Za-z0-9_.\-]+__[A-Za-z0-9_.\-]+-\d+$")

#: Never deleted, even if a caller tries to place them inside an allowed root.
_SENSITIVE_ROOTS = tuple(
    pathlib.Path(p).resolve(strict=False)
    for p in (
        r"C:\Users\rober\F2_AUTHZ",
        r"C:\Users\rober\F2_EVIDENCE",
        r"C:\Users\rober\Projects\v-horseshoe-v2",
        r"C:\Users\rober\F2_SCRATCH\backups",
        r"C:\Users\rober\F2_SCRATCH\quarantine",
        r"C:\f2_pilot\tools",
        r"C:\f2_pilot\_cache",
        r"C:\f2_pilot\build_logs",
        r"C:\f2_pilot\_preserved_workspaces",
        r"C:\f2_pilot\work",
        r"C:\f2_pilot\reports",
    )
)
_SENSITIVE_NAMES = {
    ".env", "trust_root.json", "f2_owner_signing_key.pem",
    "candidate_pool.json", "registered_tasks.json", "wheelhouse", "cache",
    "_cache", "backups", "quarantine",
}
_SENSITIVE_SUFFIXES = (".pem", ".key")


class CleanupRefused(RuntimeError):
    """A path or task is outside the cleanup contract."""


@dataclass
class CleanupResult:
    deleted: bool
    bytes_freed: int
    skipped: list[str]
    manifest_path: str | None = None
    reason: str = ""


def _is_reparse(path: pathlib.Path) -> bool:
    try:
        if path.is_symlink():
            return True
        if hasattr(path, "is_junction") and path.is_junction():
            return True
        st = os.lstat(path)
        attrs = getattr(st, "st_file_attributes", 0)
        return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise CleanupRefused(f"cannot inspect path {path}: {exc}") from exc


def _check_path(path: pathlib.Path, allowed_roots: Sequence[pathlib.Path], task_id: str | None = None) -> pathlib.Path:
    raw = pathlib.Path(path).absolute()
    for component in (raw, *raw.parents):
        if _is_reparse(component):
            raise CleanupRefused(f"symlink/junction in path: {component}")
    resolved = raw.resolve(strict=False)
    for root in allowed_roots:
        root_r = pathlib.Path(root).resolve(strict=False)
        if resolved == root_r or root_r in resolved.parents:
            break
    else:
        raise CleanupRefused(f"path outside allowlist: {resolved}")
    if task_id is not None:
        if not TASK_DIR_RE.fullmatch(task_id) or resolved.name != task_id:
            raise CleanupRefused(f"unexpected task directory: {resolved.name!r}")
    for component in (resolved, *resolved.parents):
        if _is_reparse(component):
            raise CleanupRefused(f"symlink/junction in path: {component}")
        name = component.name.lower()
        if name in _SENSITIVE_NAMES or name.endswith(_SENSITIVE_SUFFIXES):
            raise CleanupRefused(f"sensitive name in path: {component}")
    for bad in _SENSITIVE_ROOTS:
        if resolved == bad or bad in resolved.parents:
            raise CleanupRefused(f"sensitive root: {bad}")
    return resolved


def _size(path: pathlib.Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def _sha(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _evidence_ok(
    evidence_files: Sequence[tuple[pathlib.Path | str, pathlib.Path | str]],
    evidence_root: pathlib.Path,
) -> None:
    if not evidence_files:
        raise CleanupRefused("no evidence files supplied")
    root = pathlib.Path(evidence_root).resolve(strict=False)
    for src, dst in evidence_files:
        src_p = pathlib.Path(src)
        dst_p = pathlib.Path(dst)
        if not src_p.is_file() or not dst_p.is_file():
            raise CleanupRefused(f"evidence missing: {src_p} -> {dst_p}")
        try:
            dst_r = dst_p.resolve(strict=False)
            if dst_r != root and root not in dst_r.parents:
                raise CleanupRefused(f"evidence copy outside evidence root: {dst_p}")
        except OSError as exc:
            raise CleanupRefused(f"evidence path unreadable: {dst_p}: {exc}") from exc
        if _sha(src_p) != _sha(dst_p):
            raise CleanupRefused(f"evidence digest mismatch: {src_p}")


def _ledger_ok(task_id: str, ledger_paths: Sequence[pathlib.Path | str]) -> None:
    if not ledger_paths:
        raise CleanupRefused("no ledger paths supplied")
    for path in ledger_paths:
        ledger = AppendOnlyLedger(path)
        for record in ledger.records():
            if str(record.get("task_id")) == task_id and record.get("outcome"):
                return
    raise CleanupRefused(f"outcome not recorded for {task_id!r}")


def _manifest_ok(manifest_path: pathlib.Path, workspace: pathlib.Path) -> pathlib.Path:
    m = manifest_path.resolve(strict=False)
    ws = workspace.resolve(strict=False)
    if m == ws or ws in m.parents:
        raise CleanupRefused("manifest cannot live inside the deleted workspace")
    return m


def _append_manifest(
    manifest_path: pathlib.Path, reason: str, files: Iterable[tuple[pathlib.Path, int]]
) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with manifest_path.open("a", encoding="utf-8", newline="\n") as fh:
        for path, size in files:
            fh.write(
                json.dumps(
                    {
                        "schema": "f2-s8-cleanup-manifest/v1",
                        "path": str(path),
                        "bytes": size,
                        "reason": reason,
                        "deleted_at_utc": stamp,
                    },
                    sort_keys=True,
                )
                + "\n"
            )


def _delete_tree(
    path: pathlib.Path, *, keep_suffixes: tuple[str, ...] = ()
) -> tuple[list[tuple[pathlib.Path, int]], list[str]]:
    deleted: list[tuple[pathlib.Path, int]] = []
    skipped: list[str] = []
    for root, dirs, files in os.walk(path, topdown=False):
        for name in files:
            p = pathlib.Path(root) / name
            if p.name.lower() in _SENSITIVE_NAMES or p.name.lower().endswith(_SENSITIVE_SUFFIXES):
                skipped.append(f"sensitive file kept: {p}")
                continue
            if keep_suffixes and p.name.lower().endswith(keep_suffixes):
                skipped.append(f"retained file kept: {p}")
                continue
            try:
                size = p.stat().st_size
            except OSError:
                size = 0
            try:
                os.remove(p)
                deleted.append((p, size))
            except PermissionError:
                try:
                    os.chmod(p, stat.S_IWRITE)
                    os.remove(p)
                    deleted.append((p, size))
                except OSError as exc:
                    skipped.append(f"in use/permission: {p}: {exc}")
            except OSError as exc:
                skipped.append(f"in use/permission: {p}: {exc}")
        for name in dirs:
            try:
                os.rmdir(os.path.join(root, name))
            except OSError:
                pass
    try:
        os.rmdir(path)
    except OSError:
        pass
    return deleted, skipped


def delete_workspace(
    *,
    task_id: str,
    workspace: pathlib.Path | str,
    allowed_roots: Sequence[pathlib.Path | str],
    ledger_paths: Sequence[pathlib.Path | str],
    evidence_root: pathlib.Path | str,
    evidence_files: Sequence[tuple[pathlib.Path | str, pathlib.Path | str]],
    manifest_path: pathlib.Path | str,
    retry_pending: bool = False,
    stuck: bool = False,
    allow_delete: bool = False,
) -> CleanupResult:
    ws = _check_path(pathlib.Path(workspace), [pathlib.Path(r) for r in allowed_roots], task_id)
    if retry_pending or stuck:
        raise CleanupRefused(f"task {task_id!r} is retry-pending/stuck")
    _ledger_ok(task_id, ledger_paths)
    _evidence_ok(evidence_files, pathlib.Path(evidence_root))
    size = _size(ws)
    if not allow_delete:
        return CleanupResult(False, size, [], None, "dry-run")
    manifest = _manifest_ok(pathlib.Path(manifest_path), ws)
    deleted, skipped = _delete_tree(ws)
    _append_manifest(manifest, "verified_task_cleanup", deleted)
    return CleanupResult(True, sum(s for _p, s in deleted), skipped, str(manifest))


def cleanup_failed_build(
    *,
    task_id: str,
    workspace: pathlib.Path | str,
    allowed_roots: Sequence[pathlib.Path | str],
    failure_class: str,
    manifest_path: pathlib.Path | str,
    allow_delete: bool = False,
) -> CleanupResult:
    if failure_class not in {"infrastructure", "build"}:
        raise CleanupRefused(f"not an infrastructure/build failure: {failure_class!r}")
    ws = _check_path(pathlib.Path(workspace), [pathlib.Path(r) for r in allowed_roots], task_id)
    size = _size(ws)
    if not allow_delete:
        return CleanupResult(False, size, [], None, "dry-run")
    manifest = _manifest_ok(pathlib.Path(manifest_path), ws)
    deleted, skipped = _delete_tree(ws, keep_suffixes=(".log",))
    _append_manifest(manifest, "failed_build_cleanup", deleted)
    return CleanupResult(True, sum(s for _p, s in deleted), skipped, str(manifest))


def sweep_run_temp(
    *,
    root: pathlib.Path | str,
    allowed_roots: Sequence[pathlib.Path | str],
    older_than_seconds: int,
    manifest_path: pathlib.Path | str,
    allow_delete: bool = False,
    now: float | None = None,
) -> CleanupResult:
    root_r = _check_path(pathlib.Path(root), [pathlib.Path(r) for r in allowed_roots])
    current = time.time() if now is None else now
    candidates = []
    for child in sorted(root_r.iterdir()):
        if not child.is_dir() or not child.name.startswith("run-"):
            continue
        try:
            if current - child.stat().st_mtime < older_than_seconds:
                continue
        except OSError:
            continue
        candidates.append(child)
    if not allow_delete:
        return CleanupResult(False, 0, [], None, f"dry-run: {len(candidates)} dirs")
    manifest = _manifest_ok(pathlib.Path(manifest_path), root_r)
    deleted_all = []
    skipped_all = []
    for child in candidates:
        child_r = _check_path(child, [pathlib.Path(r) for r in allowed_roots])
        deleted, skipped = _delete_tree(child_r)
        deleted_all.extend(deleted)
        skipped_all.extend(skipped)
    _append_manifest(manifest, "retention_sweep", deleted_all)
    return CleanupResult(True, sum(s for _p, s in deleted_all), skipped_all, str(manifest))
