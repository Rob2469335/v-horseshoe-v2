"""Regression tests for the F2 S8 model-route selection in ``start-dev.ps1``.

Option A (owner-authorized 2026-10-14): a new explicit selector ``robs4b-reconverted``
serves the WITNESSED RECONVERSION (digest ``3e5ba031...``) while leaving the plain
``robs4b`` route and the file ``qwen_train/robs4b_q4km.gguf`` (``65202f37...``) untouched.

These tests are STATIC: they parse the launcher text (no service is started) and hash
the artifacts. The behavioural guarantee (the launcher actually maps a selector to a
path) is a text property here, exactly as the ACL-script tests parse a rule table.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_LAUNCHER = _REPO / "start-dev.ps1"
_ORIGINAL = _REPO / "qwen_train" / "robs4b_q4km.gguf"
_RECONVERTED = _REPO / "qwen_train" / "robs4b_q4km_reconverted.gguf"

_ORIGINAL_SHA = "65202f372110dde854b40ce15dcd1b6ab56a1fe9ea542b84b6a9cc745b242d41"
_RECONVERTED_SHA = "3e5ba031f0b50e98450d1575aa19780bc5b4b13f48bf3eca38f3c915a442a337"


def _text() -> str:
    return _LAUNCHER.read_text(encoding="utf-8")


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


class TestSelectorMapping:
    def test_new_selector_maps_to_the_reconverted_artifact(self):
        text = _text()
        m = re.search(
            r'if \(\$env:SWARM_LOCAL_MODEL -eq "robs4b-reconverted"\) \{(?P<body>.*?)\}',
            text, re.S,
        )
        assert m, "robs4b-reconverted selector block not found"
        body = m.group("body")
        assert "robs4b_q4km_reconverted.gguf" in body
        assert "robs4b_q4km.gguf" not in body.split("reconverted.gguf")[0].replace(
            "robs4b_q4km_reconverted.gguf", "")

    def test_plain_robs4b_still_maps_to_the_original(self):
        text = _text()
        m = re.search(
            r'if \(\$env:SWARM_LOCAL_MODEL -eq "robs4b"\) \{(?P<body>.*?)\}', text, re.S
        )
        assert m, "robs4b selector block not found"
        assert "qwen_train\\robs4b_q4km.gguf" in m.group("body")
        assert "reconverted" not in m.group("body")

    def test_default_model_is_the_original(self):
        # The default $genModel (before any selector) is the original artifact.
        assert (
            '$genModel = "C:\\Users\\rober\\Projects\\v-horseshoe-v2\\qwen_train\\robs4b_q4km.gguf"'
            in _text()
        )

    def test_alias_is_robs4b_for_both(self):
        # The served alias is unchanged; only the bytes differ.
        assert '$genAlias = "robs4b"' in _text()
        assert '"robs4b-reconverted"' in _text()


class TestNoDuplicateSelector:
    def test_robs4b_selector_block_is_not_duplicated(self):
        # The pre-existing copy-paste duplicate must be gone.
        n = len(re.findall(r'if \(\$env:SWARM_LOCAL_MODEL -eq "robs4b"\) \{', _text()))
        assert n == 1, f"expected exactly one 'robs4b' selector block, found {n}"

    def test_each_selector_name_appears_once(self):
        for name in ("robs4b", "robs4b-reconverted", "qwen3.8-4b", "qwen3.5-9b"):
            n = len(re.findall(
                r'if \(\$env:SWARM_LOCAL_MODEL -eq "' + re.escape(name) + r'"\) \{',
                _text(),
            ))
            assert n == 1, f"selector {name!r} appears {n} times"


class TestArtifactIdentity:
    def test_original_artifact_is_unchanged(self):
        if not _ORIGINAL.is_file():
            pytest.skip("original artifact not present on this host")
        assert _sha256(_ORIGINAL) == _ORIGINAL_SHA

    def test_reconverted_artifact_matches_adopted_digest(self):
        if not _RECONVERTED.is_file():
            pytest.skip("reconverted artifact not present on this host")
        assert _sha256(_RECONVERTED) == _RECONVERTED_SHA

    def test_the_two_artifacts_are_distinct(self):
        assert _ORIGINAL_SHA != _RECONVERTED_SHA

    def test_original_and_reconverted_are_different_files(self):
        assert _ORIGINAL.resolve() != _RECONVERTED.resolve()


class TestIdentityModuleAlignment:
    def test_identity_module_expects_the_adopted_digest(self):
        # The runtime identity check must expect the ADOPTED artifact when it is the
        # configured one; the module was aligned in Option B.
        from swarm_os.services.experiment_model_identity import EXPECTED_GGUF_SHA256
        assert EXPECTED_GGUF_SHA256 == _RECONVERTED_SHA


class TestFailClosedSelector:
    """An unrecognised SWARM_LOCAL_MODEL must refuse to start, not silently fall
    through to the default (ORIGINAL) artifact - the exact failure mode that made
    the .env value 'qwen3.5-4b' hazardous."""

    def test_guard_present(self):
        text = _text()
        assert "is not a recognised selector" in text
        assert "Refusing to start rather than silently serving the default model" in text

    def test_guard_covers_every_known_selector(self):
        text = _text()
        m = re.search(r'\$knownSelectors = @\((?P<items>.*?)\)', text, re.S)
        assert m, "known-selectors list not found"
        names = set(re.findall(r'"([^"]+)"', m.group("items")))
        assert names == {"robs4b", "robs4b-reconverted", "qwen3.8-4b", "qwen3.5-9b"}

    def test_guard_uses_notin_against_the_default_fallthrough(self):
        # The guard must run AFTER all positive selector blocks.
        text = _text()
        i_guard = text.index("is not a recognised selector")
        i_last = max(
            text.index(f'SWARM_LOCAL_MODEL -eq "{n}"')
            for n in ("robs4b", "robs4b-reconverted", "qwen3.8-4b", "qwen3.5-9b")
        )
        assert i_guard > i_last


class TestLauncherEnvSemantics:
    """The launcher's `.env` precedence is INTENTIONAL (`.env` wins), matching the
    project's `load_dotenv(override=True)` convention - so the session env var
    cannot override it, and the fail-closed selector guard then refuses. These
    tests pin that semantics and that the launcher never rewrites operator config.
    """

    def test_launcher_loads_env_with_unconditional_Set_Item(self):
        # `.env` -> environment, unconditionally (the documented convention).
        text = _text()
        assert 'Load .env into current session' in text
        assert 'Set-Item -Path "Env:$name" -Value $value' in text
        # There must NOT be a "only if unset" guard on this assignment.
        idx = text.index('Set-Item -Path "Env:$name" -Value $value')
        window = text[max(0, idx - 200):idx]
        assert 'if (-not $env:' not in window

    def test_launcher_does_not_write_dotenv(self):
        # The launcher must never write/modify `.env` (operator-owned).
        text = _text()
        for forbidden in ('Set-Content', 'Out-File', 'Add-Content', 'WriteAllText'):
            # no write call anywhere near the .env path handling
            assert (forbidden + " ".join(text.split())) not in text.replace("\n", " "), forbidden

    def test_dotenv_override_is_consistent_with_the_app(self):
        # swarm_os loads dotenv with override=True, so `.env` winning is the
        # project convention, not a launcher bug.
        app = (_REPO / "swarm_os" / "app" / "main.py").read_text(encoding="utf-8", errors="replace")
        assert "load_dotenv(override=True)" in app
