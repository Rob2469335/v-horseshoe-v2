"""Governed derivation of a task's ``relevant_file_set`` (Q8).

Authorized methodology (operator decision, 2026-10-04)
-----------------------------------------------------
``relevant_file_set`` = the **reference modified-file set** of the task, i.e. the
non-test source paths touched by the task's reference fix commit.

This is an **oracle-style proxy for task edit scope, NOT semantic ground
truth** and NOT "the semantically relevant files". Adopted verbatim from the
Loc2Repair precedent (arXiv 2606.30963, GeCoIn 2026 @ IJCAI-ECAI 2026), which
makes the same concession about its gold modified-file set:

    "a practical oracle-style proxy for relevant edit scope, though not a perfect
     semantic oracle: some historical edits may be incidental, and some causally
     relevant files may remain unmodified."

How the reference fix commit is identified
------------------------------------------
``base_commit`` in a SWE-bench-style task is the PARENT of the reference fix
commit, so the reference commit is a child of it. That is frequently **not
unique** -- on the verified instance ``pypa__twine-1066`` there are three
children, two of which touch exactly one non-test file each, so file counts
alone cannot disambiguate.

The unique, governed discriminator used here is the **declared reference test
patch**, which is already an authorized artifact supplied with the instance:

    the reference fix commit is the UNIQUE child of ``base_commit`` whose
    *test-file* diff set exactly equals the declared reference test patch's file
    set.

On ``pypa__twine-1066`` this selects exactly one of three candidates. The pool
row's ``num_modified_files`` is then consulted as an INDEPENDENT DIAGNOSTIC and
recorded as ``count_agreement``. It is **never an acceptance gate**: the field
carries inconsistent semantics across rows (source-only for
``pypa__twine-1066``, all-files for ``xknx__xknx-470``), so gating on it produced
demonstrably false rejections. A count discrepancy is published, not fatal.

Contamination controls (operator controls 1-9)
----------------------------------------------
* **Read-only against the task clone.** Every git invocation passes through a
  single entry point (:func:`_git`) that enforces an allowlist of read-only
  subcommands. No checkout, reset, gc, update-ref, or any write. The clone is
  never mutated.
* **No gold content is read.** Only ``git diff --name-only`` / ``--numstat``
  (path and line counts) are consulted. No gold hunk, no patch body, no file
  content.
* **Derived outside every arm workspace.** This module reads an existing clone in
  place and writes only to a caller-supplied output path. It never writes into
  an arm workspace.
* **Digest-only reference identity.** The raw reference commit id is never
  persisted; only ``sha256(repo@commit)``. A digest still detects drift if the
  derivation is ever re-run, while leaving nothing an arm could resolve.
* **No worker exposure.** Nothing produced here is ever delivered to a worker;
  the endpoint is evaluated from the trajectory after the run.

Fail-closed everywhere: no unique reference commit, a test-patch mismatch, a
file-count mismatch, an unsafe path, or any non-read-only git invocation raises.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from runtime_v2.services.task_readiness import (
    canonical_relevant_file_set,
    compute_relevant_file_set_hash,
)

__all__ = [
    "DerivationError",
    "DERIVATION_METHOD",
    "DerivationResult",
    "derive_relevant_file_set",
    "is_test_path",
    "parse_test_patch_files",
    "reference_digest",
]

DERIVATION_METHOD = "reference_modified_file_set_v1"

#: git subcommands this module is permitted to invoke. Everything here is
#: read-only. A write subcommand appearing in a derivation is a programming error
#: and is refused, because the whole contamination argument rests on the clone
#: being untouched.
READ_ONLY_GIT_SUBCOMMANDS = frozenset(
    {
        "rev-parse",
        "rev-list",
        "diff",
        "log",
        "for-each-ref",
        "cat-file",
        "show",
    }
)

_DIFF_HEADER = re.compile(r"^diff --git a/(?P<a>.+?) b/(?P<b>.+?)\s*$")


class DerivationError(RuntimeError):
    """Raised when a governed derivation cannot be completed unambiguously."""


def is_test_path(path: str) -> bool:
    """SWE-bench's test/source split.

    A path is a TEST path when it lives under a conventional test directory or
    uses conventional test-file naming. Everything else is SOURCE. This is the
    same partition SWE-bench uses to split ``patch`` from ``test_patch``, and it
    is what makes the derived set an *edit-scope proxy over source* rather than a
    set that includes the reference test change.
    """
    p = str(path or "").replace("\\", "/").strip()
    if not p:
        return False
    parts = p.split("/")
    if any(seg in ("test", "tests", "testing") for seg in parts[:-1]):
        return True
    name = parts[-1]
    if name.startswith("test_") or name.startswith("test."):
        return True
    if re.search(r"_test\.[A-Za-z0-9]+$", name):
        return True
    if name.endswith("Test.java") or name.endswith("Tests.cs"):
        return True
    return False


def parse_test_patch_files(diff_text: str) -> tuple[str, ...]:
    """Extract the file paths a unified diff touches. Path metadata only."""
    out: list[str] = []
    for line in str(diff_text or "").splitlines():
        m = _DIFF_HEADER.match(line)
        if m:
            out.append(m.group("a"))
    if not out:
        raise DerivationError(
            "declared test patch contains no `diff --git a/... b/...` headers; "
            "cannot establish the declared test-file set"
        )
    return tuple(sorted(set(out)))


def reference_digest(repo: str, commit: str) -> str:
    """SHA-256 over ``repo@commit``.

    Deliberately a digest, not the identifier: a raw commit id is a resolvable
    pointer into the contaminated clone, and operator control 4 forbids storing
    one. The digest still detects drift when the derivation is re-run.
    """
    return hashlib.sha256(f"{repo}@{commit}".encode("utf-8")).hexdigest()


def _git(repo: Path, args: Sequence[str]) -> str:
    import subprocess

    if not args:
        raise DerivationError("empty git invocation")
    sub = args[0]
    if sub not in READ_ONLY_GIT_SUBCOMMANDS:
        raise DerivationError(
            f"refusing non-read-only git subcommand {sub!r}; a derivation must "
            "never mutate the task clone"
        )
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise DerivationError(
            f"git {' '.join(args)} failed in {repo}: {proc.stderr.strip()}"
        )
    return proc.stdout


def _children_of(repo: Path, base_commit: str) -> list[str]:
    """Commits whose parent is ``base_commit``, across every ref.

    The clone's future history is fully present here precisely because the arm
    workspace has not been stripped yet -- this is a controlled, out-of-band
    derivation step, not anything an arm can reach.
    """
    base = _git(repo, ["rev-parse", "--verify", f"{base_commit}^{{commit}}"]).strip()
    children: list[str] = []
    for line in _git(repo, ["rev-list", "--all", "--children"]).splitlines():
        parts = line.split()
        if parts and parts[0] == base:
            children.extend(parts[1:])
    return sorted(set(children))


def _partition(paths: Iterable[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    src: list[str] = []
    tst: list[str] = []
    for p in paths:
        (tst if is_test_path(p) else src).append(p)
    return tuple(sorted(set(src))), tuple(sorted(set(tst)))


@dataclass(frozen=True)
class DerivationResult:
    """What a governed derivation yields. Contains no gold content."""

    instance_id: str
    repo: str
    base_commit: str
    relevant_file_set: tuple[str, ...]
    relevant_file_set_hash: str
    reference_digest: str
    derivation_method: str
    derived_at: str
    declared_test_files: tuple[str, ...]
    source_file_count: int
    source_line_count: int
    total_file_count: int
    declared_file_count: int | None
    count_agreement: str
    candidates_considered: int

    def to_dict(self) -> dict:
        return {
            "instance_id": self.instance_id,
            "repo": self.repo,
            "base_commit": self.base_commit,
            "relevant_file_set": list(self.relevant_file_set),
            "relevant_file_set_hash": self.relevant_file_set_hash,
            # Digest only -- never the raw reference commit id (operator control 4).
            "reference_digest": self.reference_digest,
            "derivation_method": self.derivation_method,
            "derived_at": self.derived_at,
            "declared_test_files": list(self.declared_test_files),
            "source_file_count": self.source_file_count,
            "source_line_count": self.source_line_count,
            "total_file_count": self.total_file_count,
            "declared_file_count": self.declared_file_count,
            "count_agreement": self.count_agreement,
            "candidates_considered": self.candidates_considered,
            "endpoint_semantics": (
                "oracle-style proxy for task edit scope; NOT semantic ground truth"
            ),
        }

    def to_endpoint(self, *, horizon_steps: int, derivation_evidence: Sequence[str]):
        """Materialise the hash-bound endpoint this derivation supports."""
        from qwen_train.f2_endpoint import freeze_endpoint

        return freeze_endpoint(
            task_id=self.instance_id,
            relevant_file_set=self.relevant_file_set,
            horizon_steps=horizon_steps,
            derivation_source=self.derivation_method,
            derivation_evidence=derivation_evidence,
        )


def derive_relevant_file_set(
    *,
    instance_id: str,
    repo: str,
    clone_path: Path | str,
    base_commit: str,
    declared_test_patch_files: Sequence[str],
    expected_source_file_count: int | None = None,
    horizon_steps: int = 12,
) -> DerivationResult:
    """Derive one task's ``relevant_file_set`` under the authorized methodology.

    ``declared_test_patch_files`` is the file set of the instance's reference
    test patch -- an already-authorized artifact, and the **authoritative**
    discriminator. ``expected_source_file_count`` is the pool row's
    ``num_modified_files``; it is recorded as an independent **diagnostic**
    (``count_agreement``) and never causes rejection of an otherwise uniquely
    identified reference commit.
    """
    clone = Path(clone_path)
    if not (clone / ".git").exists():
        raise DerivationError(f"not a git clone: {clone}")

    declared_tests = tuple(sorted(set(str(p) for p in declared_test_patch_files)))
    if not declared_tests:
        raise DerivationError("declared test-patch file set is empty")

    children = _children_of(clone, base_commit)
    if not children:
        raise DerivationError(
            f"no child commit of base {base_commit} in {clone}; the reference fix "
            "commit cannot be located, so edit scope cannot be derived"
        )

    matched: list[tuple[str, tuple[str, ...], int, int]] = []
    for child in children:
        changed = _git(clone, ["diff", "--name-only", base_commit, child]).split()
        src, tst = _partition(changed)
        if tst == declared_tests:
            matched.append(
                (child, src, len(changed), _changed_line_count(clone, base_commit, child))
            )

    if not matched:
        raise DerivationError(
            f"no child of {base_commit} has a test-file diff equal to the declared "
            f"reference test patch {list(declared_tests)}; refusing to guess the "
            "reference fix commit"
        )
    if len(matched) > 1:
        raise DerivationError(
            f"reference fix commit is AMBIGUOUS: {len(matched)} children of "
            f"{base_commit} match the declared test patch; refusing to guess"
        )

    commit, source_files, total_files, source_lines = matched[0]
    if not source_files:
        raise DerivationError(
            "reference fix commit touches no source file; an empty "
            "relevant_file_set would make the primary endpoint unmeasurable"
        )

    # File-count agreement is a RECORDED DIAGNOSTIC, not a gate.
    #
    # The pool's ``num_modified_files`` does not carry consistent semantics across
    # rows: for pypa__twine-1066 it is 1 while the reference commit modifies 2
    # files (1 source + 1 test), i.e. source-only; for xknx__xknx-470 it is 6
    # while 5 are source, i.e. all files including
    # ``test/devices_tests/sensor_test.py``. Gating on an ambiguously-defined
    # field produced demonstrably FALSE rejections, so the authoritative
    # discriminator remains the unique declared-test-patch match, and the count is
    # published alongside so a human auditor can see any discrepancy.
    declared_count = (
        int(expected_source_file_count)
        if expected_source_file_count not in (None, 0)
        else None
    )
    if declared_count is None:
        agreement = "not_declared"
    elif declared_count == len(source_files):
        agreement = "matches_source_count"
    elif declared_count == total_files:
        agreement = "matches_total_count"
    else:
        agreement = "mismatch"

    # Canonicalise through the readiness validator: this rejects unsafe paths.
    canonical = canonical_relevant_file_set(source_files)
    for p in canonical:
        if is_test_path(p):  # pragma: no cover - defensive
            raise DerivationError(f"test path leaked into the source set: {p}")

    return DerivationResult(
        instance_id=str(instance_id),
        repo=str(repo),
        base_commit=str(base_commit),
        relevant_file_set=canonical,
        relevant_file_set_hash=compute_relevant_file_set_hash(canonical),
        reference_digest=reference_digest(repo, commit),
        derivation_method=DERIVATION_METHOD,
        derived_at=datetime.now(timezone.utc).isoformat(),
        declared_test_files=declared_tests,
        source_file_count=len(canonical),
        source_line_count=source_lines,
        total_file_count=total_files,
        declared_file_count=declared_count,
        count_agreement=agreement,
        candidates_considered=len(children),
    )


def _changed_line_count(repo: Path, base: str, commit: str) -> int:
    """Total insertions+deletions over SOURCE files only. Counts, not content."""
    total = 0
    for row in _git(repo, ["diff", "--numstat", base, commit]).splitlines():
        parts = row.split("\t")
        if len(parts) < 3:
            continue
        add, dele, path = parts[0], parts[1], parts[2]
        if is_test_path(path):
            continue
        try:
            total += int(add) + int(dele)
        except ValueError:
            continue
    return total


def load_declared_test_patch(clone_parent: Path | str) -> tuple[str, ...]:
    """Read the instance's sibling ``test_patch.diff`` (path metadata only)."""
    p = Path(clone_parent) / "test_patch.diff"
    if not p.is_file():
        raise DerivationError(f"declared test patch not found: {p}")
    return parse_test_patch_files(p.read_text(encoding="utf-8", errors="replace"))


def _clone_fingerprint(clone: Path) -> str:
    """Fingerprint of a clone's HEAD + ref topology, to prove non-mutation.

    Routed through :func:`_git` so the read-only allowlist covers fingerprinting
    too. This module has exactly ONE git entry point: there is no code path that
    can reach ``git`` without passing the allowlist check.
    """
    parts = [
        _git(clone, ["rev-parse", "HEAD"]).strip(),
        _git(clone, ["for-each-ref", "--format=%(refname) %(objectname)"]).strip(),
        _git(clone, ["rev-list", "--all", "--count"]).strip(),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def build_endpoint_manifest(
    *,
    pool_path: Path | str,
    work_root: Path | str,
    out_path: Path | str,
) -> dict:
    """Derive endpoints for every task in the pool and freeze them to ``out_path``.

    Deterministic and re-runnable: the manifest is reproducible from the task
    clones, and its SHA-256 is the provenance anchor. Writes ONLY the derived
    file sets and digests -- no gold content, no resolvable reference pointer.
    """
    import io
    import json

    pool = Path(pool_path)
    work = Path(work_root)
    rows = [
        json.loads(line)
        for line in io.open(pool, encoding="utf-8")
        if line.strip()
    ]

    derived: list[dict] = []
    failures: list[dict] = []
    for row in rows:
        iid = str(row.get("instance_id") or "")
        inst = work / iid
        clone = inst / "repo"
        if not (clone / ".git").exists():
            failures.append({"instance_id": iid, "error": "clone_absent"})
            continue
        before = _clone_fingerprint(clone)
        try:
            declared = load_declared_test_patch(inst)
            res = derive_relevant_file_set(
                instance_id=iid,
                repo=str(row.get("repo") or ""),
                clone_path=clone,
                base_commit=str(row.get("base_commit") or ""),
                declared_test_patch_files=declared,
                expected_source_file_count=int(row.get("num_modified_files") or 0) or None,
            )
            after = _clone_fingerprint(clone)
            if before != after:
                raise DerivationError(
                    f"clone MUTATED during derivation ({before} -> {after})"
                )
            d = res.to_dict()
            d["clone_fingerprint_before"] = before
            d["clone_fingerprint_after"] = after
            d["clone_unmutated"] = True
            derived.append(d)
        except Exception as exc:  # noqa: BLE001 - a failure is a recorded result
            failures.append({"instance_id": iid, "error": f"{type(exc).__name__}: {exc}"})

    manifest = {
        "schema": "f2_endpoint_manifest_v1",
        "derivation_method": DERIVATION_METHOD,
        "endpoint_semantics": (
            "oracle-style proxy for task edit scope; NOT semantic ground truth"
        ),
        "operator_controls": [
            "derived before any T/X/C0 trajectory",
            "derived outside every arm workspace",
            "reference solution never exposed to the worker",
            "raw reference commit id never persisted; digest only",
            "frozen before the first confirmatory trajectory",
            "read-only git; clone fingerprint verified before and after",
        ],
        "derived": len(derived),
        "failed": len(failures),
        "entries": derived,
        "failures": failures,
    }
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    manifest["manifest_sha256"] = hashlib.sha256(out.read_bytes()).hexdigest()
    manifest["manifest_path"] = str(out)
    return manifest


def load_endpoint_manifest(
    manifest_path: Path | str,
) -> tuple[dict[str, list[str]], dict]:
    """Load a derived-endpoint manifest into a ``relevant_file_sets`` map.

    Returns ``(relevant_file_sets, provenance)`` where the map is exactly what
    :func:`qwen_train.f2_population.screen_pool_rows` accepts. Only entries that
    derived successfully are returned; failures are surfaced in ``provenance``
    so a caller can see what is still missing rather than silently screening a
    partial pool.
    """
    import json

    p = Path(manifest_path)
    if not p.is_file():
        raise DerivationError(f"endpoint manifest not found: {p}")
    data = json.loads(p.read_text(encoding="utf-8"))
    sets: dict[str, list[str]] = {}
    for entry in data.get("entries", []):
        iid = entry.get("instance_id")
        files = entry.get("relevant_file_set") or []
        if iid and files:
            sets[str(iid)] = list(files)
    provenance = {
        "schema": data.get("schema"),
        "derivation_method": data.get("derivation_method"),
        "endpoint_semantics": data.get("endpoint_semantics"),
        "derived": data.get("derived"),
        "failed": data.get("failed"),
        "failures": data.get("failures", []),
        "manifest_sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
    }
    return sets, provenance


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: derive and freeze the F2 endpoint manifest.

    ``python -m qwen_train.f2_endpoint_derivation \\
        --pool qwen_train/curriculum/swe_pool.jsonl \\
        --work-root <swe_probe_work> \\
        --out qwen_train/curriculum/f2_endpoints.json``
    """
    import argparse
    import sys

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pool", default="qwen_train/curriculum/swe_pool.jsonl")
    ap.add_argument(
        "--work-root",
        default=str(Path.home() / "Projects" / "swe_probe_work"),
        help="root containing <instance_id>/repo clones (read-only)",
    )
    ap.add_argument("--out", default="qwen_train/curriculum/f2_endpoints.json")
    args = ap.parse_args(argv)

    try:
        manifest = build_endpoint_manifest(
            pool_path=args.pool, work_root=args.work_root, out_path=args.out
        )
    except DerivationError as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2

    print(f"derived={manifest['derived']} failed={manifest['failed']}")
    print(f"manifest: {manifest['manifest_path']}")
    print(f"manifest sha256: {manifest['manifest_sha256']}")
    for f in manifest["failures"]:
        print(f"  fail {f['instance_id']}: {str(f['error'])[:100]}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())