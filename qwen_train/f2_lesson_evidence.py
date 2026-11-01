"""Fail-closed verification of a genuine ACTIVE lesson, for the preflight gate.

Why this module exists
----------------------
`f2_preflight` previously emitted `evidence.active_lesson` as a hard-coded
literal: a finding that is neither measured on this host nor read from the
repository, which the module's own contract forbids ("Every finding is either
measured on this host, read from the repository, or explicitly reported as
absent").  This module makes that gate evidence-derived.

The three dimensions are deliberately SEPARATE, because conflating them is how a
gate starts reporting READY for the wrong reason:

1. **Consistency** -- the frozen T artifact verifies, the lesson is bound into
   it, and the declared learning event satisfies the frozen Q12 shape.
2. **Authenticity** -- the promotion receipt authenticates as HMAC-SHA256 under
   the operator-provisioned receipt key (`SWARM_RECEIPT_KEY`).
3. **Authorization** -- an owner-SIGNED attestation, bound to *this* learning
   event's canonical digest and verified against the existing owner trust root
   (`f2-s8-owner-v1`), with the signer not revoked and the key epoch matched.

Only all three together reach `ALE_VERIFIED`.  Consistency alone, or consistency
plus authenticity alone, are *not* ACTIVE status: no structural check can
separate a rehearsal from a genuine learning event for a principal who holds the
receipt key, which is exactly why the authorization dimension is a separate
owner-signed act and why this module never infers it.

The verifier needs no service (no Qdrant, no network); it reads files and
verifies signatures.  It never writes, never signs, and never returns a
satisfied verdict for absent, stale, ambiguous, malformed or unverifiable input.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

SCHEMA = "f2-active-lesson-evidence/v1"
AUTHORIZATION_SCHEMA = "f2-q12-authorization/v1"
AUTHORIZATION_KIND = "q12_learning_event"

MIN_BEHAVIOURAL_RUNS = 3   # the frozen contract is ">= 3 behavioural runs"
MIN_TASKS = 3              # the frozen contract is "> 2 tasks"
DEFAULT_MAX_AGE_DAYS = 30  # a lesson must not be arbitrarily old evidence

ALE_MALFORMED = "ALE_MALFORMED"
ALE_UNREADABLE = "ALE_UNREADABLE"
ALE_STALE = "ALE_STALE"
ALE_MANIFEST_INVALID = "ALE_MANIFEST_INVALID"
ALE_LESSON_NOT_IN_MANIFEST = "ALE_LESSON_NOT_IN_MANIFEST"
ALE_HASH_MISMATCH = "ALE_HASH_MISMATCH"
ALE_RECEIPT_UNVERIFIED = "ALE_RECEIPT_UNVERIFIED"
ALE_INSUFFICIENT_RUNS = "ALE_INSUFFICIENT_RUNS"
ALE_INSUFFICIENT_TASKS = "ALE_INSUFFICIENT_TASKS"
ALE_NO_CAUSAL_RELEVANCE = "ALE_NO_CAUSAL_RELEVANCE"
ALE_POPULATION_MISMATCH = "ALE_POPULATION_MISMATCH"
ALE_NOT_AUTHORIZED = "ALE_NOT_AUTHORIZED"
ALE_CONSISTENT_UNAUTHORIZED = "ALE_CONSISTENT_UNAUTHORIZED"
ALE_VERIFIED = "ALE_VERIFIED"

_ALL_STATES = (
    ALE_MALFORMED, ALE_UNREADABLE, ALE_STALE, ALE_MANIFEST_INVALID,
    ALE_LESSON_NOT_IN_MANIFEST, ALE_HASH_MISMATCH, ALE_RECEIPT_UNVERIFIED,
    ALE_INSUFFICIENT_RUNS, ALE_INSUFFICIENT_TASKS, ALE_NO_CAUSAL_RELEVANCE,
    ALE_POPULATION_MISMATCH, ALE_NOT_AUTHORIZED, ALE_CONSISTENT_UNAUTHORIZED,
    ALE_VERIFIED,
)

#: States that mean "the record itself is well-formed but something it claims is
#: not established".  The finding must stay BLOCKING for every non-VERIFIED state.
_BLOCKING_STATES = _ALL_STATES


@dataclass(frozen=True)
class ActiveLessonVerdict:
    ok: bool
    state: str
    detail: str
    dimensions: Mapping[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "state": self.state, "detail": self.detail,
                "dimensions": dict(self.dimensions)}


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else None


def receipt_verifier_default(receipt: Mapping[str, Any], signature: str) -> bool:
    """Authenticate a promotion receipt with the operator's receipt key.

    Byte-identical to `swarm_os.services.prompt_repairer._verify_receipt`: HMAC-SHA256
    over canonical JSON.  The key VALUE is never returned, logged or stored here,
    and an absent key FAILS CLOSED.
    """
    try:
        from swarm_os.services.prompt_repairer import _receipt_key
    except Exception:  # noqa: BLE001 - fail closed
        return False
    key = _receipt_key()
    if key is None or not signature:
        return False
    body = json.dumps(dict(receipt), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")
    expected = hmac.new(key, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, str(signature))


def _verify_owner_attestation(record: Mapping[str, Any],
                              signature: Mapping[str, Any]) -> tuple[bool, str]:
    """Verify an owner-signed authorization against the existing trust root.

    Reuses `f2_authorization.load_trust_root` and the same canonicalization and
    ed25519 verification the grant path uses.  Returns (ok, reason); every error
    path is fail-closed and names the reason without printing any secret.
    """
    try:
        from qwen_train.f2_authorization import load_trust_root  # local import: optional dep
    except Exception as exc:  # noqa: BLE001
        return False, f"authorization verifier unavailable: {exc}"

    try:
        signers, revoked_keys, key_epochs, revoked_records = load_trust_root()
    except Exception as exc:  # noqa: BLE001
        return False, f"trust root unavailable: {exc}"

    kid = signature.get("key_id")
    if not isinstance(kid, str) or kid not in signers:
        return False, f"untrusted signer {kid!r}"
    if kid in revoked_keys:
        return False, f"signer key {kid!r} is revoked"
    rid = record.get("id")
    if isinstance(rid, str) and rid in revoked_records:
        return False, f"authorization {rid!r} is revoked"
    if signature.get("alg") != "ed25519":
        return False, f"unsupported signature alg {signature.get('alg')!r}"
    if signature.get("signer") != record.get("approver"):
        return False, "approver/signer identity mismatch"
    if kid in key_epochs and signature.get("key_epoch") != key_epochs[kid]:
        return False, "signer key epoch mismatch"
    raw_sig = signature.get("sig")
    if not isinstance(raw_sig, str) or not raw_sig:
        return False, "missing signature bytes"
    try:
        import base64

        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(signers[kid]))
        pub.verify(base64.b64decode(raw_sig), canonical_bytes(record))
    except Exception as exc:  # noqa: BLE001 - fail closed
        return False, f"signature verification failed ({type(exc).__name__})"
    return True, "owner authorization verified"


def verify_active_lesson_evidence(
    doc: Mapping[str, Any] | str | Path,
    *,
    receipt_verifier: Callable[[Mapping[str, Any], str], bool] | None = None,
    manifest_verifier: Callable[[Any], None] | None = None,
    expected_registration_sha256: str | None = None,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    now: datetime | None = None,
) -> ActiveLessonVerdict:
    """Verify a supplied `f2-active-lesson-evidence/v1` record.  Fail-closed."""
    now = now or datetime.now(timezone.utc)

    def bad(state: str, detail: str, dims: Mapping[str, str] | None = None) -> ActiveLessonVerdict:
        base = {"consistency": "not-established", "authenticity": "not-established",
                "authorization": "not-established"}
        if dims:
            base.update(dims)
        return ActiveLessonVerdict(False, state, detail, base)

    # ---------------------------------------------------------------- shape
    if isinstance(doc, (str, Path)):
        try:
            doc = json.loads(Path(doc).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return bad(ALE_UNREADABLE, "record path does not exist")
        except Exception as exc:  # noqa: BLE001
            return bad(ALE_MALFORMED, f"record unreadable ({type(exc).__name__})")
    if not isinstance(doc, Mapping):
        return bad(ALE_MALFORMED, "record is not an object")
    if doc.get("schema") != SCHEMA:
        return bad(ALE_MALFORMED, f"schema {doc.get('schema')!r} is not {SCHEMA!r}")

    lesson_id = str(doc.get("lesson_l_id") or "")
    lesson_hash = str(doc.get("lesson_l_hash") or "")
    if not lesson_id or not lesson_hash:
        return bad(ALE_MALFORMED, "lesson_l_id/lesson_l_hash missing")

    # ------------------------------------------------------------ freshness
    recorded_at = _parse_utc(doc.get("recorded_at"))
    if recorded_at is None:
        return bad(ALE_STALE, "recorded_at missing or not a timezone-aware ISO-8601 timestamp")
    if recorded_at > now + timedelta(minutes=5):
        return bad(ALE_STALE, "recorded_at is in the future")
    if now - recorded_at > timedelta(days=max_age_days):
        return bad(ALE_STALE, f"record is older than {max_age_days} days")

    # --------------------------------------------------------- consistency
    event = doc.get("learning_event")
    if not isinstance(event, Mapping):
        return bad(ALE_MALFORMED, "learning_event block missing")
    occurred_at = _parse_utc(event.get("occurred_at"))
    if occurred_at is None:
        return bad(ALE_STALE, "learning_event.occurred_at missing or not timezone-aware")
    if occurred_at > now + timedelta(minutes=5):
        return bad(ALE_STALE, "learning_event.occurred_at is in the future")
    if occurred_at > recorded_at + timedelta(minutes=5):
        return bad(ALE_STALE, "learning_event.occurred_at is after recorded_at")

    try:
        from runtime_v2.services.f2_freeze import dict_to_manifest, verify_manifest
    except Exception as exc:  # noqa: BLE001 - fail closed
        return bad(ALE_MANIFEST_INVALID, f"freeze verifier unavailable ({type(exc).__name__})")

    raw_manifest = doc.get("frozen_t_manifest")
    if not isinstance(raw_manifest, Mapping):
        return bad(ALE_MALFORMED, "frozen_t_manifest missing or not an object")
    try:
        artifact = dict_to_manifest(json.loads(json.dumps(raw_manifest)))
    except Exception as exc:  # noqa: BLE001
        return bad(ALE_MANIFEST_INVALID, f"frozen_t_manifest unusable ({type(exc).__name__})")
    try:
        (manifest_verifier or verify_manifest)(artifact)
    except Exception as exc:  # noqa: BLE001 - any failure is fail-closed
        return bad(ALE_MANIFEST_INVALID, f"frozen T artifact failed verification ({type(exc).__name__})")

    entries = {e.lesson_id: e for e in artifact.ordered_lessons}
    if lesson_id not in entries:  # verify_manifest already rejects duplicate lesson ids
        return bad(ALE_LESSON_NOT_IN_MANIFEST,
                   "lesson is not one of the frozen artifact's ordered_lessons")
    if entries[lesson_id].lesson_hash != lesson_hash:
        return bad(ALE_HASH_MISMATCH, "lesson_l_hash does not equal the manifest entry hash")
    if artifact.lesson_l_id is not None and artifact.lesson_l_id != lesson_id:
        return bad(ALE_HASH_MISMATCH, "lesson_l_id disagrees with the manifest's lesson_l_id")

    runs = event.get("behavioural_runs")
    tasks = event.get("task_ids")
    if not isinstance(runs, Sequence) or isinstance(runs, (str, bytes)):
        return bad(ALE_MALFORMED, "behavioural_runs must be a list")
    if not isinstance(tasks, Sequence) or isinstance(tasks, (str, bytes)):
        return bad(ALE_MALFORMED, "task_ids must be a list")
    distinct_runs = {str(r) for r in runs if str(r).strip()}
    distinct_tasks = {str(t) for t in tasks if str(t).strip()}
    if len(distinct_runs) < MIN_BEHAVIOURAL_RUNS:
        return bad(ALE_INSUFFICIENT_RUNS,
                   f"{len(distinct_runs)} distinct behavioural run(s); the frozen contract needs >= {MIN_BEHAVIOURAL_RUNS}")
    if len(distinct_tasks) < MIN_TASKS:
        return bad(ALE_INSUFFICIENT_TASKS,
                   f"{len(distinct_tasks)} distinct task(s); the frozen contract needs > 2")
    if not str(event.get("causal_relevance") or "").strip():
        return bad(ALE_NO_CAUSAL_RELEVANCE, "causal_relevance is empty")

    if expected_registration_sha256:
        binding = event.get("population_binding")
        got = str((binding or {}).get("registration_sha256") or "") if isinstance(binding, Mapping) else ""
        if got != expected_registration_sha256:
            return bad(ALE_POPULATION_MISMATCH,
                       "learning_event.population_binding.registration_sha256 does not match the pinned registration")

    consistency = "verified"

    # --------------------------------------------------------- authenticity
    promo = doc.get("promotion")
    if not isinstance(promo, Mapping):
        return bad(ALE_MALFORMED, "promotion block missing", {"consistency": consistency})
    receipt = promo.get("receipt")
    signature = str(promo.get("signature") or "")
    if not isinstance(receipt, Mapping) or not signature:
        return bad(ALE_MALFORMED, "promotion receipt/signature missing", {"consistency": consistency})
    if str(receipt.get("candidate_id") or "") != str(promo.get("candidate_id") or receipt.get("candidate_id") or ""):
        return bad(ALE_MALFORMED, "promotion candidate_id is inconsistent",
                   {"consistency": consistency})
    try:
        receipt_ok = bool((receipt_verifier or receipt_verifier_default)(receipt, signature))
    except Exception:  # noqa: BLE001 - fail closed
        receipt_ok = False
    if not receipt_ok:
        return bad(ALE_RECEIPT_UNVERIFIED,
                   "promotion receipt did not authenticate (receipt key absent or HMAC mismatch)",
                   {"consistency": consistency})
    authenticity = "verified"

    # -------------------------------------------------------- authorization
    auth = doc.get("authorization")
    if not isinstance(auth, Mapping):
        return bad(ALE_NOT_AUTHORIZED,
                   "no owner-signed authorization block is present",
                   {"consistency": consistency, "authenticity": authenticity})
    arec = auth.get("record")
    asig = auth.get("signature")
    if not isinstance(arec, Mapping) or not isinstance(asig, Mapping):
        return bad(ALE_NOT_AUTHORIZED, "authorization block is malformed",
                   {"consistency": consistency, "authenticity": authenticity})
    if arec.get("schema") != AUTHORIZATION_SCHEMA or arec.get("kind") != AUTHORIZATION_KIND:
        return bad(ALE_NOT_AUTHORIZED,
                   "authorization record is not a q12_learning_event attestation",
                   {"consistency": consistency, "authenticity": authenticity})
    if str(arec.get("lesson_l_id") or "") != lesson_id:
        return bad(ALE_NOT_AUTHORIZED, "authorization is not bound to this lesson",
                   {"consistency": consistency, "authenticity": authenticity})
    if str(arec.get("learning_event_digest") or "") != _sha256_bytes(canonical_bytes(event)):
        return bad(ALE_NOT_AUTHORIZED,
                   "authorization is not bound to this learning event (digest mismatch)",
                   {"consistency": consistency, "authenticity": authenticity})
    if expected_registration_sha256 and str(arec.get("population_registration_sha256") or "") != expected_registration_sha256:
        return bad(ALE_NOT_AUTHORIZED, "authorization population binding mismatch",
                   {"consistency": consistency, "authenticity": authenticity})
    auth_ok, auth_reason = _verify_owner_attestation(arec, asig)
    if not auth_ok:
        return bad(ALE_NOT_AUTHORIZED, f"owner authorization not established: {auth_reason}",
                   {"consistency": consistency, "authenticity": authenticity})
    if str(arec.get("approver") or "") != "f2-s8-owner-v1":
        return bad(ALE_NOT_AUTHORIZED, "authorization approver is not the owner signer",
                   {"consistency": consistency, "authenticity": authenticity})

    return ActiveLessonVerdict(
        True, ALE_VERIFIED,
        f"ACTIVE lesson {lesson_id} verified: frozen T artifact valid, lesson bound, "
        f"{len(distinct_runs)} runs / {len(distinct_tasks)} tasks with causal relevance, "
        f"promotion receipt authentic, owner authorization verified",
        {"consistency": "verified", "authenticity": "verified", "authorization": "verified"})
