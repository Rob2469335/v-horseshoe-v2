"""Distiller reproducibility provenance (R5).

A confirmatory Experiment-J record must answer "exactly which transformation
produced this lesson". Naming ``provider:model`` is insufficient: two runs
naming the same model can differ by weights checkpoint, prompt template, or
sampling parameters.

These tests prove:
* the identity carries weights digest, prompt digest, sampling config, code
  version, and can emit them as one record;
* an identity that cannot reproduce itself is REFUSED (fail closed), including
  the silent case where a provider exposes no digest;
* temperature is frozen at 0.0 and a non-frozen temperature is refused;
* the frozen sampling config is the config actually SENT (no divergence between
  recorded provenance and executed request);
* a persisted attestation carries the full record, not just ``provider:model``;
* the fake test double remains distinguishable from a real local distiller.

No distiller call is made and no model is contacted.
"""
from __future__ import annotations

import json

import pytest

from swarm_os.services import lesson_distiller as ld
from swarm_os.services.lesson_distiller import (
    DISTILLER_MAX_TOKENS,
    DISTILLER_TEMPERATURE,
    DistillerIdentity,
    LocalOnlyError,
    default_local_identity,
    distiller_prompt_digest,
    make_fake_distiller,
    make_local_distiller,
)

REAL_IDENTITY = dict(
    provider="local",
    model_id="qwen3.5-4b",
    base_url="http://127.0.0.1:8080",
    weights_digest="a" * 64,
)


class TestIdentityCarriesFullProvenance:
    def test_record_contains_every_required_field(self):
        rec = DistillerIdentity(**REAL_IDENTITY).reproducibility_record()
        for field in (
            "provider",
            "model_id",
            "qualified_id",
            "weights_digest",
            "prompt_digest",
            "temperature",
            "max_tokens",
            "top_p",
            "seed",
            "code_version",
        ):
            assert field in rec, f"missing reproducibility field {field}"

    def test_prompt_digest_is_derived_not_empty(self):
        ident = DistillerIdentity(**REAL_IDENTITY)
        assert ident.prompt_digest == distiller_prompt_digest()
        assert len(ident.prompt_digest) == 64

    def test_prompt_digest_changes_with_the_template(self):
        """The template IS part of the transformation."""
        from swarm_os.services import lesson_synthesis as ls

        original = ls._B_SYSTEM
        try:
            ls._B_SYSTEM = original + "\nEXTRA DIRECTIVE"
            assert distiller_prompt_digest() != _baseline_digest
        finally:
            ls._B_SYSTEM = original

    def test_code_version_is_populated(self):
        assert DistillerIdentity(**REAL_IDENTITY).code_version.startswith(
            "lesson_distiller:"
        )


_baseline_digest = distiller_prompt_digest()


class TestFailClosed:
    def test_missing_weights_digest_is_refused(self):
        ident = DistillerIdentity(
            provider="local", model_id="m", base_url="http://127.0.0.1:8080"
        )
        with pytest.raises(LocalOnlyError, match="weights digest"):
            ident.assert_reproducible()

    def test_silence_is_not_consent_but_explicit_absence_is(self, monkeypatch):
        monkeypatch.delenv("SWARM_DISTILLER_WEIGHTS_DIGEST_UNAVAILABLE", raising=False)
        ident = DistillerIdentity(
            provider="local", model_id="m", base_url="http://127.0.0.1:8080"
        )
        with pytest.raises(LocalOnlyError):
            ident.assert_reproducible()
        # An explicit operator statement that the provider exposes none is accepted.
        monkeypatch.setenv("SWARM_DISTILLER_WEIGHTS_DIGEST_UNAVAILABLE", "1")
        ident.assert_reproducible()

    def test_non_frozen_temperature_is_refused(self):
        ident = DistillerIdentity(**{**REAL_IDENTITY, "temperature": 0.7})
        with pytest.raises(LocalOnlyError, match="temperature"):
            ident.assert_reproducible()

    def test_empty_model_id_is_refused(self):
        ident = DistillerIdentity(**{**REAL_IDENTITY, "model_id": "  "})
        with pytest.raises(LocalOnlyError, match="model_id"):
            ident.assert_reproducible()

    def test_make_local_distiller_requires_reproducibility(self):
        with pytest.raises(LocalOnlyError):
            make_local_distiller(
                DistillerIdentity(
                    provider="local", model_id="m", base_url="http://127.0.0.1:8080"
                )
            )

    def test_make_local_distiller_accepts_a_complete_identity(self):
        d = make_local_distiller(DistillerIdentity(**REAL_IDENTITY))
        assert d.identity.qualified_id == "local:qwen3.5-4b"


class TestSamplingConfigIsWhatIsSent:
    def test_frozen_sampling_values(self):
        assert DISTILLER_TEMPERATURE == 0.0
        assert DISTILLER_MAX_TOKENS == 200

    def test_request_body_uses_the_identitys_own_config(self, monkeypatch):
        """Recorded provenance and executed request must not diverge."""
        sent: dict = {}

        class _Resp:
            def read(self):
                return json.dumps(
                    {"choices": [{"message": {"content": "rule"}}]}
                ).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def _fake_open(req, timeout=None):
            sent.update(json.loads(req.data.decode()))
            return _Resp()

        monkeypatch.setattr(ld._LOCAL_OPENER, "open", _fake_open)
        ident = DistillerIdentity(**REAL_IDENTITY)
        complete = ld._http_openai_complete(ident, timeout=1.0)
        assert complete("x") == "rule"
        assert sent["temperature"] == ident.temperature == 0.0
        assert sent["max_tokens"] == ident.max_tokens
        assert sent["top_p"] == ident.top_p
        assert sent["model"] == ident.model_id

    def test_non_local_provider_still_refused(self):
        with pytest.raises(LocalOnlyError, match="non-local"):
            make_local_distiller(
                DistillerIdentity(**{**REAL_IDENTITY, "provider": "openai"})
            )


class TestDefaultIdentityFactory:
    def test_missing_model_identity_fails_closed(self, monkeypatch):
        monkeypatch.delenv("SWARM_DISTILLER_MODEL", raising=False)
        with pytest.raises(LocalOnlyError, match="SWARM_DISTILLER_MODEL"):
            default_local_identity()

    def test_model_without_digest_fails_closed(self, monkeypatch):
        monkeypatch.setenv("SWARM_DISTILLER_MODEL", "qwen3.5-4b")
        monkeypatch.delenv("SWARM_DISTILLER_WEIGHTS_DIGEST", raising=False)
        monkeypatch.delenv("SWARM_DISTILLER_WEIGHTS_DIGEST_UNAVAILABLE", raising=False)
        with pytest.raises(LocalOnlyError, match="weights digest"):
            default_local_identity()

    def test_complete_environment_yields_a_reproducible_identity(self, monkeypatch):
        monkeypatch.setenv("SWARM_DISTILLER_MODEL", "qwen3.5-4b")
        monkeypatch.setenv("SWARM_DISTILLER_WEIGHTS_DIGEST", "b" * 64)
        ident = default_local_identity()
        ident.assert_reproducible()
        assert ident.qualified_id == "local:qwen3.5-4b"
        assert ident.weights_digest == "b" * 64


class TestFakeRemainsDistinguishable:
    def test_fake_identity_is_not_reproducible_by_default(self):
        fake = make_fake_distiller("do the thing")
        assert fake.identity.provider == "fake"
        with pytest.raises(LocalOnlyError):
            fake.identity.assert_reproducible()

    def test_fake_never_satisfies_the_reproducible_constructor(self):
        with pytest.raises(LocalOnlyError):
            make_local_distiller(make_fake_distiller("x").identity)


class TestAttestationCarriesTheRecord:
    def test_attestation_defaults_to_empty_record(self):
        from swarm_os.services.lesson_synthesis import SynthesisAttestation

        att = SynthesisAttestation(
            synthesis_version="v",
            principle_text="p",
            feature_codes=("f",),
            mechanism="m",
            evidence_ref="runs:x",
            validator_id="v1",
            validator_passed=True,
        )
        assert att.distiller_reproducibility == {}

    def test_populated_record_survives_persistence_round_trip(self):
        """F2-IMPL-AUTH-005: a POPULATED block must survive to_dict -> from_dict.

        The pre-fix defect was that ``to_dict`` omitted the field entirely, so the
        weights digest, prompt digest, frozen sampling config and code version were
        silently discarded on every save. The readiness-plan row 16 claim ("persisted
        attestation carries the full reproducibility block") was therefore NOT
        ESTABLISHED. This test exercises a POPULATED block, not the empty default.
        """
        from swarm_os.services.lesson_synthesis import SynthesisAttestation

        block = {
            "provider": "local",
            "model_id": "qwen3.5-4b",
            "qualified_id": "local:qwen3.5-4b",
            "weights_digest": "a" * 64,
            "prompt_digest": "b" * 64,
            "temperature": 0.0,
            "max_tokens": 512,
            "top_p": 1.0,
            "seed": None,
            "code_version": "deadbeef",
        }
        att = SynthesisAttestation(
            synthesis_version="v",
            principle_text="p",
            feature_codes=("f",),
            mechanism="m",
            evidence_ref="runs:x",
            validator_id="v1",
            validator_passed=True,
            distiller_reproducibility=block,
        )
        serialized = att.to_dict()
        assert "distiller_reproducibility" in serialized, (
            "to_dict must carry the structured provenance block"
        )
        restored = SynthesisAttestation.from_dict(serialized)
        assert restored is not None
        assert restored.distiller_reproducibility == block, (
            "the reproducibility block must round-trip unchanged"
        )
        # The specific fields the R5 requirement exists to preserve.
        assert restored.distiller_reproducibility["weights_digest"] == "a" * 64
        assert restored.distiller_reproducibility["prompt_digest"] == "b" * 64
        assert restored.distiller_reproducibility["temperature"] == 0.0

    def test_empty_record_round_trips_as_empty(self):
        """The default (no provenance) must not fabricate a block."""
        from swarm_os.services.lesson_synthesis import SynthesisAttestation

        att = SynthesisAttestation(
            synthesis_version="v",
            principle_text="p",
            feature_codes=("f",),
            mechanism="m",
            evidence_ref="runs:x",
            validator_id="v1",
            validator_passed=True,
        )
        restored = SynthesisAttestation.from_dict(att.to_dict())
        assert restored.distiller_reproducibility == {}
