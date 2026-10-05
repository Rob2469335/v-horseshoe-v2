"""Tests for the authoritative F2 test-outcome evaluator.

Coverage is driven by the identity hazards that actually occur in benchmark
FAIL_TO_PASS declarations: parametrisation, strict-prefix test names, class
chains, multi-suite documents, duplicate emitted identities, and non-ASCII
names. The mapping itself was verified against pytest's own ``--junitxml``
writer before being implemented.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from qwen_train import f2_evaluator as ev


# ---------------------------------------------------------------- helpers
def junit(cases: list[tuple[str, str, str | None]]) -> bytes:
    """Build a junitxml document. ``cases`` is (classname, name, tag|None)."""
    parts = ['<?xml version="1.0" encoding="utf-8"?>', "<testsuites>"]
    for classname, name, tag in cases:
        if tag is None:
            parts.append(f'<testcase classname="{classname}" name="{name}"/>')
        else:
            parts.append(
                f'<testcase classname="{classname}" name="{name}">'
                f"<{tag} message=\"m\"/></testcase>"
            )
    parts.append("</testsuites>")
    return "".join(parts).encode("utf-8")


def outcome(data: bytes, f2p, p2p=None, **kw) -> ev.EvaluationOutcome:
    return ev.evaluate_execution(data, fail_to_pass=f2p, pass_to_pass=p2p, **kw)


# ------------------------------------------------------- 1. identity mapping
class TestIdentityMapping:
    def test_plain_function(self):
        i = ev.nodeid_to_junit_identity("sub/test_things.py::test_plain")
        assert i.classname == "sub.test_things"
        assert i.name == "test_plain"

    def test_class_method(self):
        i = ev.nodeid_to_junit_identity("sub/test_things.py::TestBar::test_meth")
        assert i.classname == "sub.test_things.TestBar"
        assert i.name == "test_meth"

    def test_nested_class_chain(self):
        i = ev.nodeid_to_junit_identity("a/b/c.py::Outer::Inner::test_x")
        assert i.classname == "a.b.c.Outer.Inner"
        assert i.name == "test_x"

    def test_parametrised_preserves_brackets(self):
        i = ev.nodeid_to_junit_identity("t.py::test_param[1]")
        assert i.classname == "t"
        assert i.name == "test_param[1]"

    def test_parametrised_string_with_space(self):
        i = ev.nodeid_to_junit_identity("t.py::test_s[c d]")
        assert i.name == "test_s[c d]"

    def test_windows_separator(self):
        i = ev.nodeid_to_junit_identity("sub\\test_things.py::TestBar::test_meth")
        assert i.classname == "sub.test_things.TestBar"

    def test_non_py_path_keeps_dots(self):
        i = ev.nodeid_to_junit_identity("src/t.rs::test_x")
        assert i.classname == "src.t.rs"

    @pytest.mark.parametrize(
        "bad",
        ["", "   ", "no_colons_here", "::test_x", "a.py::", "a.py::::b"],
    )
    def test_rejects_undecomposable(self, bad):
        with pytest.raises(ev.EvaluatorError):
            ev.nodeid_to_junit_identity(bad)

    def test_identity_is_order_stable(self):
        a = ev.nodeid_to_junit_identity("x/y.py::C::t")
        b = ev.nodeid_to_junit_identity("x/y.py::C::t")
        assert a == b and hash(a) == hash(b)


# ------------------------------------------------- 2. happy path / statuses
class TestStatuses:
    def test_all_passed(self):
        data = junit([("t", "test_a", None), ("t", "test_b", None)])
        o = outcome(data, ["t.py::test_a", "t.py::test_b"])
        assert o.declared_result == "pass"
        assert o.all_fail_to_pass_passed

    def test_single_failed(self):
        data = junit([("t", "test_a", "failure")])
        o = outcome(data, ["t.py::test_a"])
        assert o.declared_result == "fail"
        assert o.fail_to_pass["t.py::test_a"] == ev.STATUS_FAILED

    def test_error_is_not_passed(self):
        data = junit([("t", "test_a", "error")])
        o = outcome(data, ["t.py::test_a"])
        assert o.declared_result == "fail"
        assert o.fail_to_pass["t.py::test_a"] == ev.STATUS_ERROR

    def test_skipped_is_not_passed(self):
        data = junit([("t", "test_a", "skipped")])
        o = outcome(data, ["t.py::test_a"])
        assert o.declared_result == "fail"
        assert o.fail_to_pass["t.py::test_a"] == ev.STATUS_SKIPPED

    def test_missing_is_not_passed(self):
        data = junit([("t", "test_a", None)])
        o = outcome(data, ["t.py::test_a", "t.py::test_absent"])
        assert o.declared_result == "fail"
        assert o.fail_to_pass["t.py::test_absent"] == ev.STATUS_MISSING

    def test_skip_beats_failure_tag(self):
        # A case carrying both is reported skipped; either way it is not passed.
        xml = (
            b'<?xml version="1.0"?><testsuites><testcase classname="t" name="test_a">'
            b'<failure message="m"/><skipped message="m"/></testcase></testsuites>'
        )
        o = outcome(xml, ["t.py::test_a"])
        assert o.declared_result == "fail"
        assert o.fail_to_pass["t.py::test_a"] == ev.STATUS_SKIPPED

    def test_pass_to_pass_regression_does_not_change_primary(self):
        data = junit([("t", "test_a", None), ("t", "test_p2p", "failure")])
        o = outcome(data, ["t.py::test_a"], ["t.py::test_p2p"])
        # Primary FAIL_TO_PASS still passes; the regression is reported.
        assert o.declared_result == "pass"
        assert o.pass_to_pass["t.py::test_p2p"] == ev.STATUS_FAILED
        assert not o.all_pass_to_pass_passed


# ----------------------------------------------- 3. prefix-collision safety
class TestPrefixCollisions:
    """Strict-prefix test names must never be confused.

    `test_run` is a strict prefix of `test_run_error_handling`. A substring or
    startswith matcher would report the wrong test's status.
    """

    def test_prefix_pair_resolves_independently(self):
        data = junit([
            ("t", "test_run", None),
            ("t", "test_run_error_handling", "failure"),
        ])
        o = outcome(data, ["t.py::test_run", "t.py::test_run_error_handling"])
        assert o.fail_to_pass["t.py::test_run"] == ev.STATUS_PASSED
        assert o.fail_to_pass["t.py::test_run_error_handling"] == ev.STATUS_FAILED
        assert o.declared_result == "fail"

    def test_prefix_pair_reverse(self):
        data = junit([
            ("t", "test_run", "failure"),
            ("t", "test_run_error_handling", None),
        ])
        o = outcome(data, ["t.py::test_run", "t.py::test_run_error_handling"])
        assert o.fail_to_pass["t.py::test_run"] == ev.STATUS_FAILED
        assert o.fail_to_pass["t.py::test_run_error_handling"] == ev.STATUS_PASSED

    def test_bare_test_name_prefix_of_parametrised(self):
        # `test_x` vs `test_x[1]`: the bracket must not be stripped.
        data = junit([("t", "test_x", None), ("t", "test_x[1]", "failure")])
        o = outcome(data, ["t.py::test_x", "t.py::test_x[1]"])
        assert o.fail_to_pass["t.py::test_x"] == ev.STATUS_PASSED
        assert o.fail_to_pass["t.py::test_x[1]"] == ev.STATUS_FAILED

    def test_same_method_name_different_classes(self):
        data = junit([("t.A", "test_x", "failure"), ("t.B", "test_x", None)])
        o = outcome(data, ["t.py::A::test_x", "t.py::B::test_x"])
        assert o.fail_to_pass["t.py::A::test_x"] == ev.STATUS_FAILED
        assert o.fail_to_pass["t.py::B::test_x"] == ev.STATUS_PASSED


# ------------------------------------------------- 4. parametrised identity
class TestParametrisation:
    def test_multiple_parameterisations_all_present(self):
        data = junit([
            ("t", "test_p[1]", None),
            ("t", "test_p[2]", None),
            ("t", "test_p[a-b]", "failure"),
        ])
        o = outcome(data, ["t.py::test_p[1]", "t.py::test_p[2]", "t.py::test_p[a-b]"])
        assert o.fail_to_pass["t.py::test_p[1]"] == ev.STATUS_PASSED
        assert o.fail_to_pass["t.py::test_p[2]"] == ev.STATUS_PASSED
        assert o.fail_to_pass["t.py::test_p[a-b]"] == ev.STATUS_FAILED
        assert o.declared_result == "fail"

    def test_one_missing_parameterisation_fails(self):
        data = junit([("t", "test_p[1]", None)])
        o = outcome(data, ["t.py::test_p[1]", "t.py::test_p[2]"])
        assert o.fail_to_pass["t.py::test_p[2]"] == ev.STATUS_MISSING
        assert o.declared_result == "fail"

    def test_all_parameterisations_pass(self):
        data = junit([("t", "test_p[1]", None), ("t", "test_p[2]", None)])
        o = outcome(data, ["t.py::test_p[1]", "t.py::test_p[2]"])
        assert o.declared_result == "pass"

    def test_parametrisation_case_sensitivity(self):
        data = junit([("t", "test_p[A]", None)])
        o = outcome(data, ["t.py::test_p[a]"])
        assert o.fail_to_pass["t.py::test_p[a]"] == ev.STATUS_MISSING


# ---------------------------------------------- 5. multiple suites / nesting
class TestMultipleSuites:
    def test_cases_across_suites_are_pooled(self):
        xml = (
            b'<?xml version="1.0"?><testsuites>'
            b'<testsuite name="s1"><testcase classname="a" name="test_1"/></testsuite>'
            b'<testsuite name="s2"><testcase classname="b" name="test_2">'
            b'<failure message="m"/></testcase></testsuite>'
            b"</testsuites>"
        )
        o = outcome(xml, ["a.py::test_1", "b.py::test_2"])
        assert o.fail_to_pass["a.py::test_1"] == ev.STATUS_PASSED
        assert o.fail_to_pass["b.py::test_2"] == ev.STATUS_FAILED
        assert o.emitted_identities == 2

    def test_bare_testsuite_root(self):
        xml = (
            b'<?xml version="1.0"?><testsuite name="s">'
            b'<testcase classname="a" name="test_1"/></testsuite>'
        )
        o = outcome(xml, ["a.py::test_1"])
        assert o.declared_result == "pass"


# ------------------------------------------------ 6. malformed / hostile XML
class TestFailClosed:
    def test_malformed_xml(self):
        with pytest.raises(ev.EvaluatorError):
            outcome(b"<testsuites><testcase", ["t.py::test_a"])

    def test_empty_bytes(self):
        with pytest.raises(ev.EvaluatorError):
            outcome(b"", ["t.py::test_a"])

    def test_no_testcases(self):
        with pytest.raises(ev.EvaluatorError):
            outcome(b'<?xml version="1.0"?><testsuites/>', ["t.py::test_a"])

    def test_testcase_missing_classname(self):
        xml = b'<?xml version="1.0"?><testsuites><testcase name="x"/></testsuites>'
        with pytest.raises(ev.EvaluatorError):
            outcome(xml, ["t.py::test_a"])

    def test_duplicate_identity_is_ambiguous(self):
        xml = (
            b'<?xml version="1.0"?><testsuites>'
            b'<testcase classname="t" name="test_a"/>'
            b'<testcase classname="t" name="test_a"><failure message="m"/></testcase>'
            b"</testsuites>"
        )
        with pytest.raises(ev.EvaluatorError, match="duplicate test identity"):
            outcome(xml, ["t.py::test_a"])

    def test_doctype_refused(self):
        xml = (
            b'<?xml version="1.0"?><!DOCTYPE t [<!ENTITY a "aaa">]>'
            b"<testsuites><testcase classname=\"m.t\" name=\"test_a\"/></testsuites>"
        )
        with pytest.raises(ev.EvaluatorError, match="DOCTYPE|ENTITY"):
            outcome(xml, ["t.py::test_a"])

    def test_size_cap(self):
        big = b"x" * (ev.MAX_JUNIT_BYTES + 1)
        with pytest.raises(ev.EvaluatorError, match="cap"):
            outcome(big, ["t.py::test_a"])

    def test_empty_declared_f2p_refused(self):
        data = junit([("t", "test_a", None)])
        with pytest.raises(ev.EvaluatorError, match="FAIL_TO_PASS is empty"):
            outcome(data, [])

    def test_none_declared_f2p_refused(self):
        data = junit([("t", "test_a", None)])
        with pytest.raises(ev.EvaluatorError):
            outcome(data, None)

    def test_bare_string_declaration_refused(self):
        data = junit([("t", "test_a", None)])
        with pytest.raises(ev.EvaluatorError, match="bare string"):
            outcome(data, "t.py::test_a")

    def test_duplicate_declared_nodeid_refused(self):
        data = junit([("t", "test_a", None)])
        with pytest.raises(ev.EvaluatorError, match="duplicate node ids"):
            outcome(data, ["t.py::test_a", "t.py::test_a"])


# ------------------------------------------------------ 7. encoding escapes
class TestEncoding:
    def test_non_ascii_name(self):
        data = (
            '<?xml version="1.0" encoding="utf-8"?><testsuites>'
            '<testcase classname="t" name="test_ünïcode"/></testsuites>'
        ).encode("utf-8")
        o = outcome(data, ["t.py::test_ünïcode"])
        assert o.declared_result == "pass"

    def test_unicode_classname(self):
        data = (
            '<?xml version="1.0" encoding="utf-8"?><testsuites>'
            '<testcase classname="t.Cläs" name="test_x"/></testsuites>'
        ).encode("utf-8")
        o = outcome(data, ["t.py::Cläs::test_x"])
        assert o.declared_result == "pass"

    def test_xml_escaped_name_roundtrip(self):
        data = (
            b'<?xml version="1.0"?><testsuites>'
            b'<testcase classname="t" name="test_a&amp;b"/></testsuites>'
        )
        o = outcome(data, ["t.py::test_a&b"])
        assert o.declared_result == "pass"

    def test_report_is_ascii_only(self):
        data = junit([("t", "test_a", None)])
        o = outcome(data, ["t.py::test_a"])
        blob = ev.render_report(o, instance_id="inst", execution_state_identity="gold")
        blob.decode("ascii")  # must not raise
        assert b"\\u" in blob or b"test_a" in blob


# ----------------------------------------------------- 8. determinism
class TestDeterminism:
    def test_report_bytes_identical_across_calls(self):
        data = junit([("t", "test_b", None), ("t", "test_a", "failure")])
        f2p = ["t.py::test_b", "t.py::test_a"]
        blobs = set()
        for _ in range(5):
            o = outcome(data, f2p)
            blobs.add(ev.render_report(o, instance_id="i", execution_state_identity="base"))
        assert len(blobs) == 1

    def test_report_key_order_independent(self):
        data = junit([("t", "test_a", None), ("t", "test_b", None)])
        o1 = outcome(data, ["t.py::test_a", "t.py::test_b"])
        o2 = outcome(data, ["t.py::test_b", "t.py::test_a"])
        assert ev.render_report(o1, instance_id="i") == ev.render_report(o2, instance_id="i")

    def test_report_contains_no_timestamp_or_path(self):
        data = junit([("t", "test_a", None)])
        blob = ev.render_report(
            outcome(data, ["t.py::test_a"]),
            instance_id="i",
            repository="o/r",
            base_commit="deadbeef",
            execution_state_identity="gold",
        )
        assert b"2026" not in blob
        assert b"C:\\" not in blob and b"C:\\\\" not in blob


# ------------------------------------------------------- 9. identity record
class TestEvaluatorIdentity:
    def test_identity_fields_complete(self):
        f = ev.identity_fields()
        assert set(f) == {
            "evaluator_id", "version", "implementation_digest", "procedure_id", "protocol_version",
        }
        assert all(isinstance(v, str) and v for v in f.values())

    def test_implementation_digest_is_lowercase_sha256(self):
        d = ev.implementation_digest()
        assert len(d) == 64
        assert d == d.lower()
        int(d, 16)

    def test_digest_matches_file_bytes(self):
        src = Path(ev.__file__).read_bytes()
        assert hashlib.sha256(src).hexdigest() == ev.implementation_digest()

    def test_no_forbidden_imports(self):
        """The evaluator must not import the F2 verifier or analysis code."""
        src = Path(ev.__file__).read_text(encoding="utf-8")
        for banned in ("f2_governance", "f2_protocol", "f2_analysis", "f2_statistics"):
            assert f"import {banned}" not in src
            assert f"from qwen_train.{banned}" not in src


# ------------------------------------------------- 10. report round-trip
class TestReportRoundTrip:
    def _report(self, f2p, p2p=None, state="gold"):
        data = junit([("t", n.split("::")[-1], t) for n, t in f2p])
        o = outcome(data, [n for n, _ in f2p], p2p, execution_state_identity=state)
        return ev.render_report(o, instance_id="i", execution_state_identity=state)

    def test_derive_pass(self):
        blob = self._report([("t.py::test_a", None)])
        assert ev.derive_result_protocol(blob, {"execution_state_identity": "gold"})[0] == "pass"

    def test_derive_fail(self):
        blob = self._report([("t.py::test_a", "failure")])
        assert ev.derive_result_protocol(blob, {})[0] == "fail"

    def test_derive_rejects_foreign_schema(self):
        blob = self._report([("t.py::test_a", None)])
        tampered = blob.replace(b'"schema":"f2_evaluator_report_v1"', b'"schema":"other_v9"')
        assert tampered != blob
        res, why = ev.derive_result_protocol(tampered, {})
        assert res is None and "schema" in why

    def test_derive_rejects_foreign_evaluator_id(self):
        blob = self._report([("t.py::test_a", None)])
        tampered = blob.replace(ev.EVALUATOR_ID.encode(), b"someone_elses_evaluator")
        res, why = ev.derive_result_protocol(tampered, {})
        assert res is None and "produced by" in why

    def test_derive_rejects_digest_mismatch(self):
        blob = self._report([("t.py::test_a", None)])
        good = ev.implementation_digest()
        tampered = blob.replace(good.encode(), ("0" * 64).encode())
        assert tampered != blob
        res, why = ev.derive_result_protocol(tampered, {})
        assert res is None and "implementation_digest" in why

    def test_derive_rejects_state_identity_mismatch(self):
        blob = self._report([("t.py::test_a", None)], state="gold")
        res, why = ev.derive_result_protocol(blob, {"execution_state_identity": "base"})
        assert res is None and "execution_state_identity" in why

    def test_derive_rejects_instance_mismatch(self):
        blob = self._report([("t.py::test_a", None)])
        tampered = blob.replace(b'"instance_id":"i"', b'"instance_id":"other"')
        res, why = ev.derive_result_protocol(tampered, {"instance_id": "i"})
        assert res is None and "instance_id" in why

    def test_derive_rejects_garbage(self):
        assert ev.derive_result_protocol(b"", {})[0] is None
        assert ev.derive_result_protocol(b"not json", {})[0] is None
        assert ev.derive_result_protocol(b"[]", {})[0] is None

    def test_parse_report_roundtrip(self):
        blob = self._report([("t.py::test_a", None)])
        p = ev.parse_report(blob)
        assert p["declared_result"] == "pass"
        assert p["fail_to_pass"] == {"t.py::test_a": "passed"}


# --------------------------------- 11. validated against real pytest output
_JUNIT_PROBE = Path(r"C:\Users\rober\AppData\Local\Temp\f2_population_comparison\junitprobe\junit.xml")


@pytest.mark.skipif(not _JUNIT_PROBE.is_file(), reason="real pytest junit probe not present")
class TestAgainstRealPytestOutput:
    """The identity mapping is checked against pytest's own writer output."""

    def _data(self) -> bytes:
        return _JUNIT_PROBE.read_bytes()

    def test_every_declared_node_resolves_against_real_output(self):
        declared = [
            "sub/test_things.py::test_plain",
            "sub/test_things.py::TestBar::test_meth",
            "sub/test_things.py::test_param[1]",
            "sub/test_things.py::test_param[2]",
            "sub/test_things.py::test_strparam[c d]",
            "sub/test_things.py::test_run",
            "sub/test_things.py::test_run_error_handling",
            "sub/test_things.py::test_ünïcode",
        ]
        o = ev.evaluate_execution(self._data(), fail_to_pass=declared)
        missing = {k: v for k, v in o.fail_to_pass.items() if v == ev.STATUS_MISSING}
        assert not missing, f"mapping failed to resolve: {missing}"
        assert o.declared_result == "pass"

    def test_real_failure_and_skip_are_not_passed(self):
        o = ev.evaluate_execution(
            self._data(),
            fail_to_pass=["sub/test_things.py::test_fail", "sub/test_things.py::test_skipped"],
        )
        assert o.fail_to_pass["sub/test_things.py::test_fail"] == ev.STATUS_FAILED
        assert o.fail_to_pass["sub/test_things.py::test_skipped"] == ev.STATUS_SKIPPED
        assert o.declared_result == "fail"

    def test_real_raising_error_reports_not_passed(self):
        o = ev.evaluate_execution(self._data(), fail_to_pass=["sub/test_things.py::test_err"])
        assert o.fail_to_pass["sub/test_things.py::test_err"] in ev.NOT_PASSED
        assert o.declared_result == "fail"

    def test_real_prefix_pair_resolves_independently(self):
        o = ev.evaluate_execution(
            self._data(),
            fail_to_pass=["sub/test_things.py::test_run", "sub/test_things.py::test_run_error_handling"],
        )
        assert o.fail_to_pass["sub/test_things.py::test_run"] == ev.STATUS_PASSED
        assert o.fail_to_pass["sub/test_things.py::test_run_error_handling"] == ev.STATUS_PASSED


# --------------------------------------- 12. producer + test-command seam
class TestProducerAndCLI:
    def test_augment_appends_junit_flag(self):
        cmd = ev.augment_test_command("pytest -q tests/", "out.xml")
        assert cmd == "pytest -q tests/ --junitxml=out.xml"

    def test_augment_is_idempotent_and_fails_closed(self):
        with pytest.raises(ev.EvaluatorError, match="already specifies"):
            ev.augment_test_command("pytest --junitxml=a.xml", "b.xml")

    def test_augment_rejects_empty_command(self):
        with pytest.raises(ev.EvaluatorError):
            ev.augment_test_command("   ", "out.xml")

    def test_produce_report_writes_canonical_bytes(self, tmp_path):
        src = tmp_path / "junit.xml"
        src.write_bytes(junit([("t", "test_a", None)]))
        dst = tmp_path / "nested" / "report.json"
        rep = ev.produce_report(
            junit_path=src,
            fail_to_pass=["t.py::test_a"],
            out_path=dst,
            instance_id="inst-1",
            repository="o/r",
            base_commit="abc123",
            execution_state_identity="gold",
        )
        assert dst.is_file()
        assert not (tmp_path / "nested" / "report.json.tmp").exists()
        assert rep["declared_result"] == "pass"
        assert rep["repository"] == "o/r"
        assert rep["base_commit"] == "abc123"
        assert rep["execution_state_identity"] == "gold"
        # Retained bytes must satisfy the registered deriver.
        assert ev.derive_result_protocol(dst.read_bytes(), {"instance_id": "inst-1"})[0] == "pass"

    def test_produce_report_is_byte_reproducible(self, tmp_path):
        src = tmp_path / "j.xml"
        src.write_bytes(junit([("t", "test_b", None), ("t", "test_a", "failure")]))
        f2p = ["t.py::test_b", "t.py::test_a"]
        a, b = tmp_path / "a.json", tmp_path / "b.json"
        for dst in (a, b):
            ev.produce_report(junit_path=src, fail_to_pass=f2p, out_path=dst, instance_id="i")
        assert a.read_bytes() == b.read_bytes()

    def test_produce_report_missing_junit_fails_closed(self, tmp_path):
        with pytest.raises(ev.EvaluatorError, match="does not exist"):
            ev.produce_report(
                junit_path=tmp_path / "absent.xml",
                fail_to_pass=["t.py::test_a"],
                out_path=tmp_path / "r.json",
            )

    def test_cli_pass_exit_zero(self, tmp_path, capsys):
        src = tmp_path / "j.xml"
        src.write_bytes(junit([("t", "test_a", None)]))
        dst = tmp_path / "r.json"
        rc = ev.main([
            "--junit", str(src), "--fail-to-pass", "t.py::test_a",
            "--out", str(dst), "--instance-id", "i", "--execution-state", "gold",
        ])
        assert rc == 0
        assert dst.is_file()
        assert ev.parse_report(dst.read_bytes())["declared_result"] == "pass"

    def test_cli_fail_exit_three(self, tmp_path):
        src = tmp_path / "j.xml"
        src.write_bytes(junit([("t", "test_a", "failure")]))
        rc = ev.main([
            "--junit", str(src), "--fail-to-pass", "t.py::test_a",
            "--out", str(tmp_path / "r.json"),
        ])
        assert rc == 3

    def test_cli_evaluator_error_exit_two(self, tmp_path, capsys):
        rc = ev.main([
            "--junit", str(tmp_path / "absent.xml"), "--fail-to-pass", "t.py::test_a",
            "--out", str(tmp_path / "r.json"),
        ])
        assert rc == 2
        assert not (tmp_path / "r.json").exists()

    def test_cli_reads_contract_file(self, tmp_path):
        src = tmp_path / "j.xml"
        src.write_bytes(junit([("t", "test_a", None)]))
        contract = tmp_path / "contract.json"
        contract.write_text(
            json.dumps({"fail_to_pass": ["t.py::test_a"], "pass_to_pass": []}),
            encoding="utf-8",
        )
        dst = tmp_path / "r.json"
        assert ev.main([
            "--junit", str(src), "--contract", str(contract), "--out", str(dst),
        ]) == 0

    def test_cli_reports_contract_without_f2p(self, tmp_path):
        src = tmp_path / "j.xml"
        src.write_bytes(junit([("t", "test_a", None)]))
        contract = tmp_path / "c.json"
        contract.write_text(json.dumps({"nope": 1}), encoding="utf-8")
        assert ev.main([
            "--junit", str(src), "--contract", str(contract),
            "--out", str(tmp_path / "r.json"),
        ]) == 2

    # NOTE: the CLI's behaviour *as an external program* is verified by direct
    # invocation during the implementation change, not here. The repository's
    # root conftest installs an autouse `global_subprocess_mock` that blocks
    # every real `subprocess.Popen`, and weakening that guard is not an
    # acceptable way to make a test pass. `main()` is exercised in-process
    # above, which covers argument handling and exit codes; the remaining
    # external-process property is covered by that manual invocation.
