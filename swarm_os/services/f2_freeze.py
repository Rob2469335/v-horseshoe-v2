"""F2 Freeze Artifact Primitives — Experiment J Phase 1.

Provides the data/provenance/integrity layer for representing and verifying
a frozen F2 delivery artifact.  Does NOT implement live-vs-frozen delivery
selection, T/X/C0 harnessing, or exclude_ids rendering.

Core principle (from F0 §3, §4, §10):

    Freeze the exact treatment artifact T first.  Later F2 X must be derived
    from that frozen artifact, not by rerunning retrieval.

Design invariants (from docs/LEARNING_EXPERIMENT_STATE.md §10.2):

    A. Freeze T first.
    B. Cross-process replay via serialized manifest, not ContextVar.
    C. One authoritative delivery abstraction.
    D. Three distinct hashes: artifact_hash, treatment_set_hash, manifest_hash.
    E. Rich freeze provenance.
    F. Fail closed on corruption/mismatch.
    G. Retrieval mutation isolation (adversarial test target).
    H. Rediscovery is trace/timestamp classification, not causality.

Hash definitions
----------------

artifact_hash
    SHA-256 of the canonical UTF-8 encoding of the *exact rendered artifact
    text*.  Same text → same hash.  One byte changed → different hash.

treatment_set_hash
    SHA-256 over the *logical treatment identity*: sorted (lesson_id,
    lesson_hash) pairs plus arm + protocol_version.  Independent from
    rendered text ordering or whitespace — it captures "which lessons in
    which order for which arm".

manifest_hash
    SHA-256 of the canonical serialized provenance manifest.  The manifest
    MUST NOT contain its own manifest_hash when computing this value.
    Canonical form: deterministic JSON (sorted keys, no whitespace, UTF-8).
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LessonEntry:
    """One lesson inside a frozen treatment artifact."""

    lesson_id: str
    lesson_hash: str  # SHA-256 hex of the lesson's rule text

    # -- required but unknown at design time --
    position: int = 0  # 1-based rendered position
    rule_text: str = ""  # the exact rendered rule string


@dataclass(frozen=True)
class FrozenArtifact:
    """Immutable record representing a frozen F2 delivery artifact.

    Every field is set once at freeze time and must never change.
    The manifest_hash is computed from the *other* fields and must not
    be included in its own computation.
    """

    # -- schema / identity --------------------------------------------------
    schema_version: int = SCHEMA_VERSION
    experiment_id: str = "experiment_j"
    protocol_version: str = "f2_v1"

    # -- environment provenance ---------------------------------------------
    git_sha: str = ""
    model_name: str = ""
    task_id: str = ""

    # -- treatment identity -------------------------------------------------
    arm: str = ""  # "T" | "X" | "C0" — at freeze time this is always "T"
    ordered_lessons: tuple[LessonEntry, ...] = ()
    lesson_l_id: str | None = None
    lesson_l_hash: str | None = None

    # -- artifact content ---------------------------------------------------
    rendered_artifact: str = ""  # exact text to be delivered to the worker

    # -- three distinct hashes ----------------------------------------------
    artifact_hash: str = ""
    treatment_set_hash: str = ""
    manifest_hash: str = ""  # must NOT be used as input to its own computation

    # -- timestamps / promotion provenance ----------------------------------
    freeze_timestamp: float = 0.0  # time.time()
    promotion_proof_ref: str | None = None  # opaque reference to HMAC proof

    # -- persistence --------------------------------------------------------
    content_address: str = ""  # filename-friendly prefix of manifest_hash

    # ------------------------------------------------------------------
    # Canonical serialization helpers
    # ------------------------------------------------------------------

    def _canonical_lessons_payload(self) -> list[dict]:
        """Deterministic lesson list for hashing — sorted by lesson_id."""
        entries = []
        for le in self.ordered_lessons:
            entry: dict = {
                "lesson_id": le.lesson_id,
                "lesson_hash": le.lesson_hash,
                "position": le.position,
            }
            if le.rule_text:
                entry["rule_text"] = le.rule_text
            entries.append(entry)
        entries.sort(key=lambda e: e["lesson_id"])
        return entries

    def _canonical_payload(self, *, include_manifest_hash: bool = False) -> dict:
        """Canonical JSON-safe dict for hashing / serialization.

        Field order is explicit.  All values are JSON-primitive-safe.
        """
        payload: dict = {
            "schema_version": self.schema_version,
            "experiment_id": self.experiment_id,
            "protocol_version": self.protocol_version,
            "git_sha": self.git_sha,
            "model_name": self.model_name,
            "task_id": self.task_id,
            "arm": self.arm,
            "ordered_lessons": self._canonical_lessons_payload(),
            "lesson_l_id": self.lesson_l_id,
            "lesson_l_hash": self.lesson_l_hash,
            "rendered_artifact": self.rendered_artifact,
            "artifact_hash": self.artifact_hash,
            "treatment_set_hash": self.treatment_set_hash,
            "freeze_timestamp": self.freeze_timestamp,
            "promotion_proof_ref": self.promotion_proof_ref,
        }
        if include_manifest_hash:
            payload["manifest_hash"] = self.manifest_hash
        return payload

    def canonical_bytes(self, *, include_manifest_hash: bool = False) -> bytes:
        """Canonical UTF-8 bytes of the deterministic JSON representation."""
        payload = self._canonical_payload(include_manifest_hash=include_manifest_hash)
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return canonical.encode("utf-8")

    # ------------------------------------------------------------------
    # Hash computation
    # ------------------------------------------------------------------

    def compute_artifact_hash(self) -> str:
        """SHA-256 of the exact rendered artifact text."""
        return hashlib.sha256(self.rendered_artifact.encode("utf-8")).hexdigest()

    def compute_treatment_set_hash(self) -> str:
        """SHA-256 of the logical treatment identity.

        Input: sorted (lesson_id, lesson_hash) pairs + arm + protocol_version.
        Independent from rendered text content.
        """
        identity = {
            "arm": self.arm,
            "protocol_version": self.protocol_version,
            "lessons": [
                {"id": le.lesson_id, "hash": le.lesson_hash}
                for le in sorted(self.ordered_lessons, key=lambda l: l.lesson_id)
            ],
        }
        canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def compute_manifest_hash(self) -> str:
        """SHA-256 of the canonical manifest *excluding* manifest_hash itself."""
        return hashlib.sha256(self.canonical_bytes(include_manifest_hash=False)).hexdigest()

    def compute_content_address(self, manifest_hash: str) -> str:
        """Deterministic content-addressed filename prefix."""
        return manifest_hash[:16]


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def freeze_artifact(
    *,
    rendered_artifact: str,
    arm: str = "T",
    ordered_lessons: tuple[LessonEntry, ...] = (),
    lesson_l_id: str | None = None,
    lesson_l_hash: str | None = None,
    git_sha: str = "",
    model_name: str = "",
    task_id: str = "",
    experiment_id: str = "experiment_j",
    protocol_version: str = "f2_v1",
    promotion_proof_ref: str | None = None,
    freeze_timestamp: float | None = None,
) -> FrozenArtifact:
    """Create a FrozenArtifact with all three hashes computed."""
    import time as _time

    ts = freeze_timestamp if freeze_timestamp is not None else _time.time()

    artifact = FrozenArtifact(
        experiment_id=experiment_id,
        protocol_version=protocol_version,
        git_sha=git_sha,
        model_name=model_name,
        task_id=task_id,
        arm=arm,
        ordered_lessons=ordered_lessons,
        lesson_l_id=lesson_l_id,
        lesson_l_hash=lesson_l_hash,
        rendered_artifact=rendered_artifact,
        freeze_timestamp=ts,
        promotion_proof_ref=promotion_proof_ref,
    )

    a_hash = artifact.compute_artifact_hash()
    t_hash = artifact.compute_treatment_set_hash()

    # Rebuild with hashes filled in (frozen dataclass requires new instance)
    artifact = FrozenArtifact(
        schema_version=artifact.schema_version,
        experiment_id=artifact.experiment_id,
        protocol_version=artifact.protocol_version,
        git_sha=artifact.git_sha,
        model_name=artifact.model_name,
        task_id=artifact.task_id,
        arm=artifact.arm,
        ordered_lessons=artifact.ordered_lessons,
        lesson_l_id=artifact.lesson_l_id,
        lesson_l_hash=artifact.lesson_l_hash,
        rendered_artifact=artifact.rendered_artifact,
        artifact_hash=a_hash,
        treatment_set_hash=t_hash,
        freeze_timestamp=artifact.freeze_timestamp,
        promotion_proof_ref=artifact.promotion_proof_ref,
    )

    m_hash = artifact.compute_manifest_hash()
    ca = artifact.compute_content_address(m_hash)

    return FrozenArtifact(
        schema_version=artifact.schema_version,
        experiment_id=artifact.experiment_id,
        protocol_version=artifact.protocol_version,
        git_sha=artifact.git_sha,
        model_name=artifact.model_name,
        task_id=artifact.task_id,
        arm=artifact.arm,
        ordered_lessons=artifact.ordered_lessons,
        lesson_l_id=artifact.lesson_l_id,
        lesson_l_hash=artifact.lesson_l_hash,
        rendered_artifact=artifact.rendered_artifact,
        artifact_hash=artifact.artifact_hash,
        treatment_set_hash=artifact.treatment_set_hash,
        manifest_hash=m_hash,
        freeze_timestamp=artifact.freeze_timestamp,
        promotion_proof_ref=artifact.promotion_proof_ref,
        content_address=ca,
    )


# ---------------------------------------------------------------------------
# Serialization / Deserialization
# ---------------------------------------------------------------------------

def serialize_manifest(artifact: FrozenArtifact) -> bytes:
    """Canonical UTF-8 bytes of the complete manifest (includes manifest_hash)."""
    return artifact.canonical_bytes(include_manifest_hash=True)


def manifest_to_dict(artifact: FrozenArtifact) -> dict:
    """JSON-safe dict of the manifest for persistence."""
    return json.loads(serialize_manifest(artifact))


def dict_to_manifest(data: dict) -> FrozenArtifact:
    """Reconstruct a FrozenArtifact from a persisted dict."""
    lessons = tuple(
        LessonEntry(
            lesson_id=str(le["lesson_id"]),
            lesson_hash=str(le["lesson_hash"]),
            position=int(le.get("position", 0)),
            rule_text=le.get("rule_text", ""),
        )
        for le in data.get("ordered_lessons", [])
    )
    content_address = str(data.get("content_address", ""))
    if not content_address and "manifest_hash" in data:
        content_address = data["manifest_hash"][:16]
    return FrozenArtifact(
        schema_version=int(data.get("schema_version", 0)),
        experiment_id=str(data.get("experiment_id", "")),
        protocol_version=str(data.get("protocol_version", "")),
        git_sha=str(data.get("git_sha", "")),
        model_name=str(data.get("model_name", "")),
        task_id=str(data.get("task_id", "")),
        arm=str(data.get("arm", "")),
        ordered_lessons=lessons,
        lesson_l_id=data.get("lesson_l_id"),
        lesson_l_hash=data.get("lesson_l_hash"),
        rendered_artifact=str(data.get("rendered_artifact", "")),
        artifact_hash=str(data.get("artifact_hash", "")),
        treatment_set_hash=str(data.get("treatment_set_hash", "")),
        manifest_hash=str(data.get("manifest_hash", "")),
        freeze_timestamp=float(data.get("freeze_timestamp", 0.0)),
        promotion_proof_ref=data.get("promotion_proof_ref"),
        content_address=content_address,
    )


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

class FreezeVerificationError(Exception):
    """Raised when a freeze manifest fails verification."""


def verify_manifest(artifact: FrozenArtifact) -> None:
    """Verify the integrity and provenance of a frozen artifact.

    Raises FreezeVerificationError on any failure.
    Must NOT silently fall back to live retrieval (fail-closed).
    """
    # 1. Schema version
    if artifact.schema_version != SCHEMA_VERSION:
        raise FreezeVerificationError(
            f"Unsupported schema version: {artifact.schema_version} "
            f"(expected {SCHEMA_VERSION})"
        )

    # 2. Artifact hash
    recomputed_a = artifact.compute_artifact_hash()
    if recomputed_a != artifact.artifact_hash:
        raise FreezeVerificationError(
            f"Artifact hash mismatch: stored={artifact.artifact_hash} "
            f"recomputed={recomputed_a}"
        )

    # 3. Treatment set hash
    recomputed_t = artifact.compute_treatment_set_hash()
    if recomputed_t != artifact.treatment_set_hash:
        raise FreezeVerificationError(
            f"Treatment set hash mismatch: stored={artifact.treatment_set_hash} "
            f"recomputed={recomputed_t}"
        )

    # 4. Manifest hash (must exclude self)
    recomputed_m = artifact.compute_manifest_hash()
    if recomputed_m != artifact.manifest_hash:
        raise FreezeVerificationError(
            f"Manifest hash mismatch: stored={artifact.manifest_hash} "
            f"recomputed={recomputed_m}"
        )

    # 5. Structural validity
    if not artifact.experiment_id:
        raise FreezeVerificationError("Missing experiment_id")
    if not artifact.arm:
        raise FreezeVerificationError("Missing arm")
    if not artifact.rendered_artifact and artifact.arm != "C0":
        raise FreezeVerificationError(
            f"Empty rendered_artifact for non-C0 arm {artifact.arm!r}"
        )

    # 6. Lesson structure
    seen_ids: set[str] = set()
    for le in artifact.ordered_lessons:
        if not le.lesson_id:
            raise FreezeVerificationError("Lesson entry with empty lesson_id")
        if not le.lesson_hash:
            raise FreezeVerificationError(
                f"Lesson {le.lesson_id} with empty lesson_hash"
            )
        if le.lesson_id in seen_ids:
            raise FreezeVerificationError(
                f"Duplicate lesson_id: {le.lesson_id}"
            )
        seen_ids.add(le.lesson_id)

    # 7. L-provenance consistency
    if artifact.lesson_l_id is not None:
        if artifact.lesson_l_id not in seen_ids:
            raise FreezeVerificationError(
                f"lesson_l_id {artifact.lesson_l_id!r} not in ordered_lessons"
            )
        if artifact.lesson_l_hash is None:
            raise FreezeVerificationError(
                "lesson_l_id set but lesson_l_hash is None"
            )

    # 8. Content address consistency
    expected_ca = artifact.compute_content_address(artifact.manifest_hash)
    if artifact.content_address and artifact.content_address != expected_ca:
        raise FreezeVerificationError(
            f"content_address mismatch: stored={artifact.content_address} "
            f"expected={expected_ca}"
        )


# ---------------------------------------------------------------------------
# Atomic persistence
# ---------------------------------------------------------------------------

def persist_manifest(artifact: FrozenArtifact, directory: Path) -> Path:
    """Atomically write the manifest to disk.

    Path: ``directory/<content_address>_f2_freeze.json``

    Uses temp-file + os.replace for atomicity.
    Returns the final path on success.
    """
    directory.mkdir(parents=True, exist_ok=True)

    filename = f"{artifact.content_address}_f2_freeze.json"
    final_path = directory / filename

    canonical = json.dumps(
        manifest_to_dict(artifact),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        indent=2,
    )

    fd, tmp_path = tempfile.mkstemp(
        dir=str(directory),
        prefix=f".{filename}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(canonical)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(final_path))
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    return final_path


def load_manifest(path: Path) -> FrozenArtifact:
    """Load and reconstruct a FrozenArtifact from a persisted JSON file."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return dict_to_manifest(data)
