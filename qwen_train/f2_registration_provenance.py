"""Verify that the operative registration manifest still binds its artifacts.

The binding being checked
-------------------------
``qwen_train/curriculum/f2_registration/registration_manifest.json`` is the
git-tracked, ``operative: true`` record of what is registered. It carries an
``artifacts_sha256`` map over the three files that
``data/f2_population/registration/`` holds (git-ignored, so their bytes live
only on disk).

Which bytes, exactly, matters. ``registration_artifacts`` records the digest of
the **canonical row form** -- ``json.dumps(rows, sort_keys=True,
separators=(",", ":"), ensure_ascii=False)`` -- while the registrar *writes*
those same rows to disk **indented** (``indent=2`` plus a trailing newline). The
two are different serializations of identical content, so their file hashes
naturally differ. A verifier that hashed file bytes would report drift for a
perfectly bound registration.

That is not hypothetical: the first version of this module did exactly that, and
on 2026-10-14 reported all three artifacts as drifted when in fact **all three
bound correctly**. The on-disk rows were shown to be identical to a fresh
deterministic recomputation from the pinned inputs -- same instance_ids, same
order, zero differing fields -- and the registrar's own row digests matched the
manifest. The false alarm had to be corrected before it could be trusted to
detect a real one.

What this module does and does not do
-------------------------------------
It is a **read-only verifier**. It never rewrites the manifest, never
regenerates the registration, and never touches the population. Changing either
side would alter the registered experimental population, which requires
explicit authorization; this only reports whether the two sides still agree.

Two further properties are checked, because the per-file digests alone cannot
see them: ``first_30`` must literally be the head of
``full_ordered_metadata``, and ``registration_content_sha256`` must be
re-derivable with the registrar's own ``registration_artifacts`` over the rows
on disk and the manifest's pinned ``inputs``. That digest had been left
``NOT ESTABLISHED`` because a re-typed reconstruction of the content dict could
not reproduce it -- the dict inside the registrar carries
``f2_task_plan.SCHEMA_VERSION`` (``f2_task_order_v1``), which is NOT the
manifest's own top-level ``schema_version`` (``f2_registration_v1``). Calling
the registrar's function instead of retyping its dict reproduces the recorded
digest exactly, and this check keeps it reproduced.

Exit status is fail-closed: any mismatch, missing artifact, or unreadable file
returns a non-zero code, so it can gate a pipeline.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

__all__ = [
    "DEFAULT_REGISTRATION_DIR",
    "DEFAULT_MANIFEST_PATH",
    "ArtifactCheck",
    "ProvenanceReport",
    "verify_registration",
    "main",
]

DEFAULT_REGISTRATION_DIR = Path("data") / "f2_population" / "registration"
DEFAULT_MANIFEST_PATH = (
    Path("qwen_train") / "curriculum" / "f2_registration" / "registration_manifest.json"
)

#: manifest key -> on-disk file name
_ARTIFACT_FILES = {
    "first_30": "first_30.json",
    "full_ordered_metadata": "full_ordered_metadata.json",
    "excluded": "excluded.json",
}


@dataclass(frozen=True)
class ArtifactCheck:
    key: str
    path: str
    recorded: str
    actual: str
    match: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "path": self.path, "recorded": self.recorded,
            "actual": self.actual, "match": self.match, "detail": self.detail,
        }


@dataclass
class ProvenanceReport:
    manifest_path: str
    registration_dir: str
    registration_id: str = ""
    operative: bool = False
    checks: list[ArtifactCheck] = field(default_factory=list)
    count_checks: list[ArtifactCheck] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def bound(self) -> bool:
        """True only when every digest AND every count agrees."""
        return (
            not self.errors
            and bool(self.checks)
            and all(c.match for c in self.checks)
            and all(c.match for c in self.count_checks)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_path": self.manifest_path,
            "registration_dir": self.registration_dir,
            "registration_id": self.registration_id,
            "operative": self.operative,
            "bound": self.bound,
            "artifacts": [c.to_dict() for c in self.checks],
            "counts": [c.to_dict() for c in self.count_checks],
            "errors": list(self.errors),
        }


def _row_digest(path: Path) -> str:
    """The digest the registrar actually binds: the CANONICAL ROW FORM.

    ``registration_artifacts`` records ``_digest(rows)``, i.e.
    ``json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False)``.
    The registrar *writes* the same rows to disk in indented form
    (``indent=2`` + trailing newline), so the file bytes are a different
    serialization of identical content and must NOT be hashed directly.

    Hashing file bytes here was a defect in the first version of this verifier:
    it reported "digest drift" for a perfectly bound registration, because the
    two serializations naturally hash differently. A binding check that does not
    reproduce the binder's own semantics is not a check -- it is a false alarm,
    and a false alarm that blocks an admission pipeline is as damaging as a
    missed one.
    """
    rows = json.loads(path.read_text(encoding="utf-8"))
    blob = json.dumps(rows, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _json_len(path: Path) -> int:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return len(payload) if isinstance(payload, list) else -1


def _consistency_checks(
    manifest: dict[str, Any], reg: Path, report: ProvenanceReport
) -> None:
    """Cross-file and content-digest properties the per-file digests cannot see.

    Two properties, both read-only:

    * ``first_30`` must literally be the first ``len(first_30)`` rows of
      ``full_ordered_metadata``. Each file's digest is bound separately, so two
      internally consistent but mutually inconsistent artifacts would still
      hash "correctly"; only a cross-file comparison catches that.
    * ``registration_content_sha256`` must be re-derivable with the registrar's
      OWN function (``f2_task_plan.registration_artifacts``) over the rows on
      disk and the manifest's pinned ``inputs``. This closes the one item that
      had been left ``NOT ESTABLISHED``: the digest looked unresolvable only
      because the content dict inside the registrar carries
      ``f2_task_plan.SCHEMA_VERSION`` (``f2_task_order_v1``), which is NOT the
      manifest's own top-level ``schema_version``
      (``f2_registration_v1``). Recomputing with the registrar's function, not
      with a re-typed dict, reproduces the recorded digest exactly.
    """
    full = reg / _ARTIFACT_FILES["full_ordered_metadata"]
    first = reg / _ARTIFACT_FILES["first_30"]
    excl = reg / _ARTIFACT_FILES["excluded"]

    ordered: Any = None
    excluded: Any = None
    head: Any = None
    lists_ok = full.is_file() and first.is_file() and excl.is_file()
    if lists_ok:
        try:
            ordered = json.loads(full.read_text(encoding="utf-8"))
            excluded = json.loads(excl.read_text(encoding="utf-8"))
            head = json.loads(first.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            report.errors.append(f"consistency check unreadable: {exc}")
            lists_ok = False

    if lists_ok:
        if isinstance(ordered, list) and isinstance(head, list):
            match = bool(head) and head == ordered[: len(head)]
            detail = "" if match else "first_30 is not the head of full_ordered_metadata"
            actual = "prefix" if match else "not a prefix"
        else:
            match, actual = False, "not a list"
            detail = "an artifact is not a list"
        report.checks.append(ArtifactCheck(
            key="first_30_prefix", path=str(first),
            recorded=f"first {len(head)} of {len(ordered)}"
                     if isinstance(head, list) and isinstance(ordered, list)
                     else "n/a",
            actual=actual, match=match, detail=detail,
        ))

    recorded = str(manifest.get("registration_content_sha256") or "")
    if not lists_ok:
        return  # the per-artifact checks already reported the missing file
    if not recorded:
        report.checks.append(ArtifactCheck(
            key="registration_content_sha256", path=str(full),
            recorded="", actual="", match=True,
            detail="manifest records no content digest; artifact digests "
                   "still verified",
        ))
        return
    if not (isinstance(ordered, list) and isinstance(excluded, list)):
        report.checks.append(ArtifactCheck(
            key="registration_content_sha256", path=str(full),
            recorded=recorded, actual="", match=False,
            detail="an artifact is not a list; content digest not recomputable",
        ))
        return
    try:
        from qwen_train.f2_task_plan import registration_artifacts

        top_n = len(head)
        arts = registration_artifacts(
            ordered,
            excluded,
            inputs=manifest.get("inputs") or {},
            top_n=top_n,
            generated_at=manifest.get("generated_at"),
        )
    except Exception as exc:  # noqa: BLE001 - fail closed, never raise
        report.errors.append(f"content digest could not be recomputed: {exc}")
        return
    actual = str(arts.get("content_sha256") or "")
    report.checks.append(ArtifactCheck(
        key="registration_content_sha256", path=str(full),
        recorded=recorded, actual=actual, match=recorded == actual,
        detail="" if recorded == actual else "content digest drift",
    ))


def verify_registration(
    registration_dir: Path | str = DEFAULT_REGISTRATION_DIR,
    manifest_path: Path | str = DEFAULT_MANIFEST_PATH,
) -> ProvenanceReport:
    """Compare the manifest's digests and counts against the files on disk."""
    reg = Path(registration_dir)
    man_path = Path(manifest_path)
    report = ProvenanceReport(
        manifest_path=str(man_path), registration_dir=str(reg)
    )

    if not man_path.is_file():
        report.errors.append(f"manifest not found: {man_path}")
        return report
    try:
        manifest = json.loads(man_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        report.errors.append(f"manifest unreadable: {exc}")
        return report

    report.registration_id = str(manifest.get("registration_id") or "")
    report.operative = bool(manifest.get("operative"))

    recorded = manifest.get("artifacts_sha256") or {}
    for key, filename in _ARTIFACT_FILES.items():
        path = reg / filename
        want = str(recorded.get(key) or "")
        if not path.is_file():
            report.checks.append(ArtifactCheck(
                key=key, path=str(path), recorded=want, actual="",
                match=False, detail="artifact missing",
            ))
            continue
        actual = _row_digest(path)
        report.checks.append(ArtifactCheck(
            key=key, path=str(path), recorded=want, actual=actual,
            match=bool(want) and want == actual,
            detail="" if want == actual else "canonical row digest drift",
        ))

    # Counts: the manifest's own numbers must match what is on disk.
    counts = manifest.get("counts") or {}
    full = reg / _ARTIFACT_FILES["full_ordered_metadata"]
    if full.is_file() and "registered" in counts:
        actual_n = _json_len(full)
        report.count_checks.append(ArtifactCheck(
            key="registered", path=str(full),
            recorded=str(counts["registered"]), actual=str(actual_n),
            match=str(counts["registered"]) == str(actual_n),
            detail="" if str(counts["registered"]) == str(actual_n)
            else "entry count differs",
        ))
    excl = reg / _ARTIFACT_FILES["excluded"]
    if excl.is_file() and "excluded" in counts:
        actual_n = _json_len(excl)
        report.count_checks.append(ArtifactCheck(
            key="excluded", path=str(excl),
            recorded=str(counts["excluded"]), actual=str(actual_n),
            match=str(counts["excluded"]) == str(actual_n),
            detail="" if str(counts["excluded"]) == str(actual_n)
            else "entry count differs",
        ))
    _consistency_checks(manifest, reg, report)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Verify the operative registration still binds its artifacts")
    ap.add_argument("--registration-dir", default=str(DEFAULT_REGISTRATION_DIR))
    ap.add_argument("--manifest", default=str(DEFAULT_MANIFEST_PATH))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    report = verify_registration(args.registration_dir, args.manifest)
    if args.json:
        print(json.dumps(report.to_dict(), indent=1, sort_keys=True))
    else:
        print(f"manifest : {report.manifest_path}")
        print(f"register : {report.registration_id} (operative={report.operative})")
        for c in report.checks + report.count_checks:
            flag = "OK  " if c.match else "FAIL"
            print(f"  [{flag}] {c.key:<24} recorded={c.recorded[:20]}… "
                  f"actual={c.actual[:20]}… {c.detail}")
        for err in report.errors:
            print(f"  [ERR ] {err}")
        print(f"BOUND: {report.bound}")
    return 0 if report.bound else 1


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
