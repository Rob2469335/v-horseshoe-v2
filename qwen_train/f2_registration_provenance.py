"""Verify that the operative registration manifest still binds its artifacts.

The problem this closes
-----------------------
``qwen_train/curriculum/f2_registration/registration_manifest.json`` is the
git-tracked, ``operative: true`` record of what is registered. It carries an
``artifacts_sha256`` map over the three files that ``data/f2_population/registration/``
holds (git-ignored, so their bytes live only on disk).

Nothing previously re-checked that binding. On 2026-10-14 this module found
that all three digests had drifted: the files on disk were already in the
canonical serialization the registrar writes (``indent=2``, ``sort_keys``,
trailing ``\\n``), so the drift is a **content** change, not a formatting one.
The registration the manifest describes and the registration on disk are not
the same registration, and nothing said so.

That breaks S8 admission evidence: an admission would be bound to a manifest
whose digests describe files that no longer exist. Detecting it silently, at a
boundary nobody checks, is exactly how a provenance gap survives.

What this module does and does not do
-------------------------------------
It is a **read-only verifier**. It never rewrites the manifest, never
regenerates the registration, and never touches the population. Changing either
side would alter the registered experimental population, which requires
explicit authorization; this only reports whether the two sides still agree.

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


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_len(path: Path) -> int:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return len(payload) if isinstance(payload, list) else -1


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
        actual = _sha256_file(path)
        report.checks.append(ArtifactCheck(
            key=key, path=str(path), recorded=want, actual=actual,
            match=bool(want) and want == actual,
            detail="" if want == actual else "digest drift",
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
