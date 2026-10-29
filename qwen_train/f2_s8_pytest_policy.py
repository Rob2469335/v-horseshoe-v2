"""Fail-closed pytest version policy for Experiment J-F2 S8.

Why this module exists
----------------------
The builder installs one pytest per task environment and the evaluator grades
JUnit produced by it.  A silently different pytest is an environment change
that can move a task's outcome, so the version is *policy*, not an
implementation detail.

The previous resolver (``f2_s8_acquisition.select_pytest_version``) had five
demonstrated defects:

1. A bare ``pytest`` declaration produced a VALID result with
   ``pin="pytest"`` and ``version=None`` -- an unpinned install recorded as if
   it were policy.
2. ``declared_python_minor`` defaulted to ``(3, 10)``, so an omitted argument
   silently assumed a policy value.
3. Only exact ``==`` pins were compared.  A range-only declaration resolved to
   no version at all, and an exact pin combined with a range took whichever
   declaration happened to be read first (order-dependent).
4. Constraint sources were partial: a pytest pin in ``setup.cfg``,
   ``setup.py``, ``tox.ini``, ``[project].dependencies`` or a lockfile was never
   seen, so the project's own bound could be silently violated.
5. Nothing was recorded for the declaration set, its hashes, the intersection
   or the owner's admissibility decision, so the result could not be audited
   after the fact.

Design rules
------------
* **No execution.**  Every declaration source is read statically.  ``setup.py``
  is parsed with :mod:`ast` and evaluated with :func:`ast.literal_eval` only;
  a dynamic declaration is a blocking problem, never a guess.
* **Order independence.**  The intersection is built from a *sorted set* of
  specifier strings, so declaration order can never change the answer.
* **Admissibility is separate from compatibility.**  A release whose
  ``Requires-Python`` accepts the declared minor is *technically* usable; it is
  only *admissible* if the owner ratified it.  No such ratification is recorded
  in this repository (decision D1), so :data:`OPERATIVE_PYTEST_ADMISSIBILITY` is
  ``owner_ratified=False`` and the build boundary refuses to run under it until
  the owner supplies one.
* **Pinned metadata.**  Release metadata is a fixed in-repository table with a
  snapshot id and per-release source URL.  Its provenance digest is reported as
  ``NOT ESTABLISHED`` rather than fabricated.

Sources consulted (accessed 2026-10-22)
---------------------------------------
* PEP 440 -- version specifiers, including ``~=`` and ``==X.Y.*`` wildcards.
* PEP 508 -- requirement strings, markers and direct references.
* PEP 621 / PEP 735 -- ``[project].dependencies``, ``optional-dependencies``,
  ``[dependency-groups]`` with ``include-group``.
* PEP 517 -- ``[build-system].requires`` (build backend requirements).
* pip requirements-file format -- nested ``-r`` / ``-c``.
* Poetry and Hatch project metadata -- groups, extras and environment deps.
"""
from __future__ import annotations

import ast
import configparser
import hashlib
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from packaging.markers import default_environment
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

__all__ = [
    "ADMISSIBILITY_EVIDENCE_SCHEMA",
    "ADMISSIBILITY_PROVENANCE_SCHEMA",
    "ADMISSIBILITY_SCHEMA",
    "DEFAULT_PYTEST_PIN",
    "METADATA_SNAPSHOT_ID",
    "OPERATIVE_PYTEST_ADMISSIBILITY",
    "PYTEST_COMPATIBILITY_FALLBACK",
    "PYTEST_RELEASE_METADATA",
    "PYTEST_ADMISSIBILITY_PATH",
    "PYTEST_ADMISSIBILITY_PROVENANCE_PATH",
    "PytestAdmissibility",
    "PytestPolicyError",
    "collect_pytest_declarations",
    "find_explicit_project_pytest_pins",
    "load_pytest_admissibility",
    "require_ratified_pytest_admissibility",
    "resolve_pytest_admissibility",
    "select_pytest_version",
]

ADMISSIBILITY_SCHEMA = "f2-s8-pytest-admissibility/v1"
ADMISSIBILITY_PROVENANCE_SCHEMA = "f2-s8-pytest-admissibility-provenance/v1"
ADMISSIBILITY_EVIDENCE_SCHEMA = "f2-s8-pytest-admissibility-evidence/v1"

#: Identifier for the fixed release-metadata table below.  It is *pinned* (the
#: table travels with the code) but its external provenance digest is not
#: recorded, so that fact is reported as NOT ESTABLISHED in every result.
METADATA_SNAPSHOT_ID = "pypi-pytest-json-2026-10-22"
METADATA_PROVENANCE_DIGEST: str | None = None

#: Fixed, verified PyPI release metadata for the pytest releases that may be
#: installed.  ``admitted_minors`` is the release's TECHNICAL compatibility with
#: the declared interpreter minor, derived from its ``Requires-Python``; it is
#: not an authorization to use that release.
PYTEST_RELEASE_METADATA: dict[str, dict[str, Any]] = {
    "7.0.1": {
        "version": "7.0.1",
        "requires_python": ">=3.6",
        "pypi_url": "https://pypi.org/pypi/pytest/7.0.1/json",
        "retrieved_utc": "2026-10-22T12:00:00Z",
        "admitted_minors": [(3, 6), (3, 7), (3, 8), (3, 9), (3, 10)],
    },
    "7.4.4": {
        "version": "7.4.4",
        "requires_python": ">=3.7",
        "pypi_url": "https://pypi.org/pypi/pytest/7.4.4/json",
        "retrieved_utc": "2026-10-22T12:00:00Z",
        "admitted_minors": [(3, 7), (3, 8), (3, 9), (3, 10), (3, 11), (3, 12)],
    },
    "8.3.5": {
        "version": "8.3.5",
        "requires_python": ">=3.8",
        "pypi_url": "https://pypi.org/pypi/pytest/8.3.5/json",
        "retrieved_utc": "2026-10-22T12:00:00Z",
        "admitted_minors": [(3, 8), (3, 9), (3, 10), (3, 11), (3, 12), (3, 13), (3, 14)],
    },
    "8.4.0": {
        # Added 2026-10-22 after a population scan: two retained lockfiles
        # (awslabs__mcp-1154, stanfordnlp__dspy-8739) resolve pytest 8.4.0, which
        # the catalogue did not describe, so the resolver could only refuse them.
        # Verified against https://pypi.org/pypi/pytest/8.4.0/json:
        # requires_python ">=3.9", yanked=false, uploaded 2025-06-02.
        # SECURITY: GHSA-6w46-j5rx-g56g / CVE-2025-71176 affects pytest <=9.0.2
        # on UNIX only (the /tmp/pytest-of-{user} pattern); the S8 host and
        # guest are Windows, and the fix lands in 9.0.3.
        "version": "8.4.0",
        "requires_python": ">=3.9",
        "pypi_url": "https://pypi.org/pypi/pytest/8.4.0/json",
        "retrieved_utc": "2026-10-22T00:00:00Z",
        "uploaded_utc": "2025-06-02T17:36:27Z",
        "admitted_minors": [(3, 9), (3, 10), (3, 11), (3, 12), (3, 13), (3, 14)],
    },
    "8.4.1": {
        "version": "8.4.1",
        "requires_python": ">=3.9",
        "pypi_url": "https://pypi.org/pypi/pytest/8.4.1/json",
        "retrieved_utc": "2026-10-22T12:00:00Z",
        "admitted_minors": [(3, 9), (3, 10), (3, 11), (3, 12), (3, 13), (3, 14)],
    },
    "8.4.2": {
        "version": "8.4.2",
        "requires_python": ">=3.9",
        "pypi_url": "https://pypi.org/pypi/pytest/8.4.2/json",
        "retrieved_utc": "2026-10-22T12:00:00Z",
        "admitted_minors": [(3, 9), (3, 10), (3, 11), (3, 12), (3, 13), (3, 14)],
    },
    "9.1.1": {
        "version": "9.1.1",
        "requires_python": ">=3.10",
        "pypi_url": "https://pypi.org/pypi/pytest/9.1.1/json",
        "retrieved_utc": "2026-10-22T12:00:00Z",
        "admitted_minors": [(3, 10), (3, 11), (3, 12), (3, 13), (3, 14), (3, 15)],
    },
}

#: Deterministic compatibility fallback when the declared minor is below the
#: default pin's floor and no project declaration exists.
PYTEST_COMPATIBILITY_FALLBACK: dict[tuple[int, int], str] = {
    (3, 6): "pytest==7.0.1",
    (3, 7): "pytest==7.4.4",
}

DEFAULT_PYTEST_PIN = "pytest==8.3.5"

#: The FIXED location of an owner-ratified admissibility document.  Fixed, not
#: configurable, for the same reason the declared-environments sidecar is: a
#: path that a process can point anywhere makes the boundary optional.
PYTEST_ADMISSIBILITY_PATH = Path(r"C:\f2_pilot\pytest_admissibility.json")

#: The provenance record that MUST accompany the policy document.  A policy
#: without it is a hand-edited JSON file, not a recorded owner decision, so the
#: loader refuses it.  Mirrors the declared-environments sidecar/provenance pair.
PYTEST_ADMISSIBILITY_PROVENANCE_PATH = Path(
    r"C:\f2_pilot\pytest_admissibility_provenance.json")

#: Evidence artifacts the provenance record must bind by hash (the population
#: scan and any policy review the owner used).  An evidence record that cannot
#: be found, or whose bytes changed, fails the whole ratification closed.
ADMISSIBILITY_EVIDENCE_ROLES = ("population_scan", "release_metadata")


class PytestPolicyError(RuntimeError):
    """The pytest policy cannot be applied without an owner decision."""


@dataclass(frozen=True)
class PytestAdmissibility:
    """The release set policy is allowed to choose from.

    ``owner_ratified`` is deliberately separate from ``versions``: a policy can
    enumerate technically usable releases without the owner having ratified
    them, and the build boundary must be able to tell those two states apart.
    """

    versions: tuple[str, ...]
    source: str
    sha256: str = ""
    owner_ratified: bool = False
    ratification_note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "versions": list(self.versions),
            "source": self.source,
            "sha256": self.sha256,
            "owner_ratified": self.owner_ratified,
            "ratification_note": self.ratification_note,
        }


#: The releases the existing builder would choose from.  This is the OPERATIVE
#: policy, not an owner ratification: no governance document in this repository
#: records which pytest releases are admissible (decision D1), so the build
#: boundary (:func:`require_ratified_pytest_admissibility`) refuses to run under
#: it until the owner supplies a ratified set.
OPERATIVE_PYTEST_ADMISSIBILITY = PytestAdmissibility(
    versions=tuple(sorted(PYTEST_RELEASE_METADATA, key=Version)),
    source="qwen_train.f2_s8_pytest_policy.PYTEST_RELEASE_METADATA",
    sha256="",
    owner_ratified=False,
    ratification_note=(
        "D1 NOT RATIFIED: no owner-ratified pytest release set is recorded in "
        "this repository. The operative table is a technical catalogue only."),
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _digest_or_raise(path: Path, what: str) -> str:
    if not path.is_file():
        raise PytestPolicyError(f"{what} missing: {path}")
    return _sha256_file(path)


def load_pytest_admissibility(
    path: Path | str,
    *,
    provenance_path: Path | str | None = None,
) -> PytestAdmissibility:
    """Load an OWNER-ratified release set, bound to its provenance record.

    Ratification is a *pair* of artifacts, exactly as the declared-environments
    sidecar is: the policy document lists the releases, and a provenance record
    states who ratified it and binds -- by SHA-256 -- the policy file and the
    evidence the decision rests on.  A policy document without a valid
    provenance record is a hand-edited JSON file, so it is refused: otherwise
    writing one file would open the build gate with no recorded decision.
    """
    policy_path = Path(path)
    provenance = Path(provenance_path or PYTEST_ADMISSIBILITY_PROVENANCE_PATH)
    policy_sha = _digest_or_raise(policy_path, "pytest admissibility policy")
    provenance_sha = _digest_or_raise(
        provenance, "pytest admissibility provenance")

    try:
        body = json.loads(policy_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise PytestPolicyError(f"pytest admissibility policy is not JSON: {exc}") from exc
    if body.get("schema") != ADMISSIBILITY_SCHEMA:
        raise PytestPolicyError(
            f"pytest admissibility schema {body.get('schema')!r} != {ADMISSIBILITY_SCHEMA!r}")
    ratified_by = str(body.get("ratified_by") or "").strip()
    if not ratified_by:
        raise PytestPolicyError("pytest admissibility policy has no ratified_by")
    raw_versions = body.get("versions")
    if not isinstance(raw_versions, list) or not raw_versions:
        raise PytestPolicyError("pytest admissibility policy has no versions")
    versions: list[str] = []
    for value in raw_versions:
        text = str(value).strip()
        if text not in PYTEST_RELEASE_METADATA:
            raise PytestPolicyError(
                f"pytest admissibility names unverified release {text!r}; "
                "only releases with fixed metadata may be admitted")
        versions.append(text)

    try:
        prov = json.loads(provenance.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise PytestPolicyError(
            f"pytest admissibility provenance is not JSON: {exc}") from exc
    if not isinstance(prov, Mapping):
        raise PytestPolicyError("pytest admissibility provenance is not an object")
    if prov.get("schema") != ADMISSIBILITY_PROVENANCE_SCHEMA:
        raise PytestPolicyError(
            f"pytest admissibility provenance schema {prov.get('schema')!r} "
            f"!= {ADMISSIBILITY_PROVENANCE_SCHEMA!r}")
    if str(prov.get("ratified_by") or "").strip() != ratified_by:
        raise PytestPolicyError(
            "pytest admissibility provenance ratifying authority does not match "
            "the policy document")
    bound = prov.get("policy")
    if not isinstance(bound, Mapping):
        raise PytestPolicyError("pytest admissibility provenance.policy is not an object")
    bound_path = Path(str(bound.get("path") or ""))
    if not bound_path.is_file() or bound_path.resolve() != policy_path.resolve():
        raise PytestPolicyError(
            "pytest admissibility provenance names a different policy document")
    if str(bound.get("sha256") or "").lower() != policy_sha:
        raise PytestPolicyError("pytest admissibility policy digest mismatch")

    evidence = prov.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise PytestPolicyError(
            "pytest admissibility provenance binds no evidence artifacts")
    roles: set[str] = set()
    for record in evidence:
        if not isinstance(record, Mapping):
            raise PytestPolicyError("pytest admissibility evidence entry is not an object")
        role = str(record.get("role") or "")
        if role not in ADMISSIBILITY_EVIDENCE_ROLES:
            raise PytestPolicyError(
                f"pytest admissibility evidence role {role!r} is not recognised")
        if record.get("schema") != ADMISSIBILITY_EVIDENCE_SCHEMA:
            raise PytestPolicyError(
                f"pytest admissibility evidence {role!r} has schema "
                f"{record.get('schema')!r}")
        artifact = Path(str(record.get("path") or ""))
        actual = _digest_or_raise(artifact, f"pytest admissibility evidence {role}")
        if actual != str(record.get("sha256") or "").lower():
            raise PytestPolicyError(
                f"pytest admissibility evidence {role!r} digest mismatch")
        roles.add(role)
    missing = [role for role in ADMISSIBILITY_EVIDENCE_ROLES if role not in roles]
    if missing:
        raise PytestPolicyError(
            f"pytest admissibility provenance is missing evidence roles: {missing}")

    return PytestAdmissibility(
        versions=tuple(sorted(set(versions), key=Version)),
        source=str(policy_path),
        sha256=policy_sha,
        owner_ratified=True,
        ratification_note=(
            f"ratified_by={ratified_by}; provenance={provenance} "
            f"sha256={provenance_sha}; evidence roles={sorted(roles)}; "
            "documents are hash-bound but NOT cryptographically signed "
            "(GOVERNANCE GAP)"),
    )


def resolve_pytest_admissibility(
    explicit: PytestAdmissibility | None = None,
) -> PytestAdmissibility:
    """The admissibility policy in force: explicit, then the fixed sidecar.

    The sidecar lives at a FIXED path (:data:`PYTEST_ADMISSIBILITY_PATH`), the
    same convention as the declared-environments sidecar.  There is deliberately
    no environment override: an arbitrary ``*_PATH`` variable would let any
    process with shell access unblock the build boundary, which is the class of
    hole this gate exists to close.
    """
    if explicit is not None:
        return explicit
    if PYTEST_ADMISSIBILITY_PATH.is_file():
        return load_pytest_admissibility(PYTEST_ADMISSIBILITY_PATH)
    return OPERATIVE_PYTEST_ADMISSIBILITY


def require_ratified_pytest_admissibility(
    explicit: PytestAdmissibility | None = None,
) -> PytestAdmissibility:
    """Return a RATIFIED policy or raise -- the fail-closed execution gate.

    This is what the builder calls before it installs anything.  Absent an
    owner-ratified set, no task can be built, because a build under an
    unratified pytest policy would produce an environment whose outcome cannot
    be attributed to the task.
    """
    policy = resolve_pytest_admissibility(explicit)
    if not policy.owner_ratified:
        raise PytestPolicyError(
            "PYTEST_ADMISSIBILITY_NOT_RATIFIED: no owner-ratified pytest release set "
            "is recorded (decision D1); refusing to build under an unratified policy. "
            f"Set F2_PYTEST_ADMISSIBILITY to a ratified policy document "
            f"({ADMISSIBILITY_SCHEMA}) or obtain the owner decision. "
            f"operative source: {policy.source}")
    return policy


# ---------------------------------------------------------------------------
# Static declaration collection
# ---------------------------------------------------------------------------
def _acq():
    """The acquisition module, imported lazily.

    ``f2_s8_acquisition`` imports this module at module scope, so this module
    must import it only inside functions.  Every parser it owns is reused rather
    than copied, because a second reader of the same project file is exactly how
    the builder and the classifier drift apart.
    """
    from qwen_train import f2_s8_acquisition as acquisition

    return acquisition


def _requirement_name(raw: Any) -> str:
    """The distribution a raw declaration names, without parsing its specifier.

    Deliberately tolerant: a MALFORMED pytest declaration (``pytest[unclosed``)
    must still be recognised as a pytest declaration so it can be refused
    explicitly instead of being skipped as "not pytest".
    """
    text = str(raw or "").strip()
    if not text or text.startswith("-"):
        return ""
    head = text.split(";", 1)[0].strip()
    match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", head)
    return canonicalize_name(match.group(1)) if match else ""


def _setup_cfg_declarations(
    path: Path,
) -> tuple[list[tuple[str, str, str]], list[dict[str, str]]]:
    """``setup.cfg`` install requirements and extras, in declaration order."""
    parser = configparser.ConfigParser(interpolation=None)
    problems: list[dict[str, str]] = []
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, OSError) as exc:
        return [], [{"code": "setup_cfg_unreadable", "scope": "blocking",
                     "detail": f"{path.name}: {exc}"}]
    found: list[tuple[str, str, str]] = []
    if parser.has_option("options", "install_requires"):
        for line in parser.get("options", "install_requires").splitlines():
            line = line.strip()
            if line and not line.startswith("-"):
                found.append(("setup.cfg:[options].install_requires",
                              "setup_cfg", line))
    if parser.has_section("options.extras_require"):
        for extra in parser.options("options.extras_require"):
            for line in parser.get("options.extras_require", extra).splitlines():
                line = line.strip()
                if line and not line.startswith("-"):
                    found.append((f"setup.cfg:[options.extras_require].{extra}",
                                  "setup_cfg", line))
    return found, problems


def _literal_requirement_strings(
    node: ast.AST,
) -> list[str] | None:
    """String entries of a literal list/tuple, or ``None`` when not literal."""
    try:
        value = ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if isinstance(item, str)]
    return None


class _SetupPyFileResolver:
    """Resolve the two common STATIC file-read declaration shapes.

    ``NAME = get_requires("requirements.txt")`` (a local helper that reads its
    argument with ``open``) and ``NAME = open("requirements.txt").readlines()``
    are pure data flow over plain files inside the project.  They are resolved
    here so a project that keeps its requirements in a file is not refused as
    "dynamic"; every other shape still returns ``None`` (blocking).
    """

    def __init__(self, setup_py: Path, tree: ast.AST) -> None:
        self._root = Path(setup_py).parent.resolve()
        self._readers = self._reader_functions(tree)
        self._bindings = self._bindings_from(tree)

    @staticmethod
    def _reader_functions(tree: ast.AST) -> set[str]:
        readers: set[str] = set()
        for node in getattr(tree, "body", []):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.args.args:
                continue
            arg = node.args.args[0].arg
            for child in ast.walk(node):
                if (isinstance(child, ast.Call)
                        and isinstance(child.func, ast.Name)
                        and child.func.id == "open"
                        and child.args
                        and isinstance(child.args[0], ast.Name)
                        and child.args[0].id == arg):
                    readers.add(node.name)
                    break
        return readers

    def _path(self, literal: Any) -> Path | None:
        if not isinstance(literal, str) or not literal.strip():
            return None
        candidate = (self._root / literal).resolve()
        if candidate != self._root and self._root not in candidate.parents:
            return None          # outside the project: never read
        return candidate

    @staticmethod
    def _lines(path: Path) -> list[str] | None:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        out = []
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(line)
        return out

    @staticmethod
    def _open_literal(node: ast.AST) -> str | None:
        """``open("p")`` or a method chain over it (read/readlines/split...)."""
        current = node
        while True:
            if isinstance(current, ast.Attribute):
                current = current.value
                continue
            if isinstance(current, ast.Call):
                func = current.func
                if isinstance(func, ast.Name) and func.id == "open":
                    if (current.args and isinstance(current.args[0], ast.Constant)
                            and isinstance(current.args[0].value, str)):
                        return current.args[0].value
                    return None
                if isinstance(func, ast.Attribute):
                    current = func.value
                    continue
                return None
            return None

    def _resolve(self, node: ast.AST, bindings: Mapping[str, list[str]]):
        if isinstance(node, ast.Name):
            return bindings.get(node.id)
        if isinstance(node, ast.Call):
            func = node.func
            if (isinstance(func, ast.Name) and func.id in self._readers
                    and node.args and isinstance(node.args[0], ast.Constant)):
                path = self._path(node.args[0].value)
                return self._lines(path) if path is not None else None
            literal = self._open_literal(node)
            if literal is not None:
                path = self._path(literal)
                return self._lines(path) if path is not None else None
        return None

    def _bindings_from(self, tree: ast.AST) -> dict[str, list[str]]:
        bindings: dict[str, list[str]] = {}
        for node in getattr(tree, "body", []):
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)):
                resolved = self._resolve(node.value, bindings)
                if resolved is not None:
                    # A later non-resolvable rebinding (e.g. a strip loop) does
                    # not erase the file it was derived from.
                    bindings[node.targets[0].id] = resolved
        return bindings

    def entries(self, node: ast.AST) -> list[str] | None:
        return self._resolve(node, self._bindings)


def _setup_py_declarations(
    path: Path,
) -> tuple[list[tuple[str, str, str]], list[dict[str, str]]]:
    """Statically analysable ``setup()`` declarations -- never executed.

    ``install_requires``, ``extras_require``, ``tests_require`` and
    ``setup_requires`` are read when their value is a literal, or when it is a
    plain file read (``open(...).readlines()`` / a local ``get_requires``
    helper).  A ``**kwargs`` expansion or any other non-literal value means the
    declaration is produced by code, which this layer refuses to evaluate: it is
    reported as BLOCKING so the caller cannot pretend the project declared
    nothing.
    """
    try:
        tree = ast.parse(Path(path).read_text(encoding="utf-8", errors="replace"))
    except SyntaxError as exc:
        return [], [{"code": "setup_py_unparsable", "scope": "blocking",
                     "detail": f"{path.name}: {exc}"}]
    found: list[tuple[str, str, str]] = []
    problems: list[dict[str, str]] = []
    saw_setup = False
    resolver = _SetupPyFileResolver(path, tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (func.attr if isinstance(func, ast.Attribute)
                else func.id if isinstance(func, ast.Name) else "")
        if name != "setup":
            continue
        saw_setup = True
        for keyword in node.keywords:
            if keyword.arg is None:
                problems.append({"code": "setup_py_dynamic_declaration",
                                 "scope": "blocking",
                                 "detail": f"{path.name}:**kwargs"})
                continue
            if keyword.arg not in ("install_requires", "extras_require",
                                   "tests_require", "setup_requires"):
                continue
            entries = _literal_requirement_strings(keyword.value)
            if entries is None:
                entries = resolver.entries(keyword.value)
            if entries is None:
                # ``extras_require`` is a dict, which literal_eval CAN read.
                if keyword.arg == "extras_require":
                    try:
                        mapping = ast.literal_eval(keyword.value)
                    except (ValueError, SyntaxError):
                        mapping = None
                    if isinstance(mapping, Mapping):
                        for extra, members in mapping.items():
                            for member in members or ():
                                if isinstance(member, str):
                                    found.append((
                                        f"{path.name}:extras_require[{extra}]",
                                        "setup_py", member))
                        continue
                problems.append({"code": "setup_py_dynamic_declaration",
                                 "scope": "blocking",
                                 "detail": f"{path.name}:{keyword.arg}"})
                continue
            if keyword.arg == "extras_require":
                continue
            for entry in entries:
                found.append((f"{path.name}:{keyword.arg}", "setup_py", entry))
    if not saw_setup:
        problems.append({"code": "setup_py_no_setup_call", "scope": "advisory",
                         "detail": path.name})
    return found, problems


def _tox_declarations(
    path: Path,
) -> tuple[list[tuple[str, str, str]], list[dict[str, str]]]:
    """``tox.ini`` ``deps`` for ``[testenv]`` and every ``[testenv:*]``."""
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, OSError) as exc:
        return [], [{"code": "tox_ini_unreadable", "scope": "blocking",
                     "detail": f"{path.name}: {exc}"}]
    found: list[tuple[str, str, str]] = []
    for section in parser.sections():
        if section != "testenv" and not section.startswith("testenv:"):
            continue
        if not parser.has_option(section, "deps"):
            continue
        for line in parser.get(section, "deps").splitlines():
            line = line.strip()
            if line and not line.startswith("-"):
                found.append((f"tox.ini:[{section}].deps", "tox_ini", line))
    return found, []


def _lockfile_record(project: Path) -> dict[str, Any] | None:
    """The pytest resolution a governing lockfile records, if any.

    Read-only and static: TOML lockfiles are parsed by :mod:`tomllib`, the
    Pipfile lock by :mod:`json`.  A lock that names pytest more than once with
    different versions is returned as ambiguous; the caller refuses it.
    """
    candidates = ("uv.lock", "poetry.lock", "pdm.lock")
    for name in candidates:
        path = Path(project) / name
        if not path.is_file():
            continue
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (tomllib.TOMLDecodeError, OSError):
            return {"file": name, "sha256": _sha256_file(path), "found": True,
                    "version": None, "versions": [], "error": "lockfile is not valid TOML"}
        versions: list[str] = []
        for package in data.get("package") or ():
            if not isinstance(package, Mapping):
                continue
            if canonicalize_name(str(package.get("name") or "")) != "pytest":
                continue
            version = str(package.get("version") or "").strip()
            if version:
                versions.append(version)
        return {"file": name, "sha256": _sha256_file(path), "found": True,
                "version": versions[0] if len(versions) == 1 else None,
                "versions": sorted(set(versions), key=Version),
                "error": None if versions else "lockfile does not record pytest"}
    pipfile = Path(project) / "Pipfile.lock"
    if pipfile.is_file():
        try:
            data = json.loads(pipfile.read_text(encoding="utf-8"))
        except ValueError:
            return {"file": "Pipfile.lock", "sha256": _sha256_file(pipfile),
                    "found": True, "version": None, "versions": [],
                    "error": "lockfile is not valid JSON"}
        versions = []
        for section in ("default", "develop"):
            entry = (data.get(section) or {}).get("pytest")
            if isinstance(entry, Mapping):
                version = str(entry.get("version") or "").lstrip("=").strip()
                if version:
                    versions.append(version)
        return {"file": "Pipfile.lock", "sha256": _sha256_file(pipfile),
                "found": True,
                "version": versions[0] if len(versions) == 1 else None,
                "versions": sorted(set(versions), key=Version),
                "error": None if versions else "lockfile does not record pytest"}
    return None


def collect_pytest_declarations(
    *,
    project: Path | None = None,
    dependency_inputs: Mapping[str, Any] | None = None,
    runtime_dependencies: Sequence[str] = (),
    test_requirements: Sequence[str] | None = None,
    adjustment: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Every pytest declaration the project's STATIC metadata contains.

    Returns ``{"declarations", "sources", "problems", "lock"}``:

    ``declarations``  one record per ``pytest`` requirement, with its source,
                      kind, verbatim text and the declaring file's SHA-256;
    ``sources``       the files read, each with its SHA-256;
    ``problems``      BLOCKING problems mean the caller must fail closed;
    ``lock``          the governing lockfile's pytest resolution, if any.
    """
    acquisition = _acq()
    project_path = Path(project) if project is not None else None
    declarations: list[dict[str, str]] = []
    sources: list[dict[str, Any]] = []
    problems: list[dict[str, str]] = []
    lock: dict[str, Any] | None = None

    def add(source: str, kind: str, raw: Any, sha: str = "") -> None:
        if _requirement_name(raw) == "pytest":
            declarations.append({
                "source": str(source), "kind": str(kind),
                "raw": str(raw).strip(), "sha256": str(sha or ""),
            })

    def add_many(source: str, kind: str, entries: Any, sha: str = "") -> None:
        for entry in entries or ():
            if isinstance(entry, Mapping):
                problems.append({"code": "unsupported_declaration_entry",
                                 "scope": "blocking",
                                 "detail": f"{source}: {sorted(entry)}"})
                continue
            add(source, kind, entry, sha)

    if adjustment and adjustment.get("pytest_pin"):
        add("adjustment", "adjustment", adjustment["pytest_pin"])
    if test_requirements is not None:
        add_many("test_requirements", "test_requirements", test_requirements)

    # [project].dependencies -- the project install brings these in, so a pin
    # here is a real bound on the environment.
    add_many("pyproject.toml:[project].dependencies", "project_dependencies",
             runtime_dependencies)

    if dependency_inputs:
        extras = dependency_inputs.get("extras") or {}
        for name in sorted(extras):
            add_many(f"pyproject.toml:[project.optional-dependencies].{name}",
                     "optional_dependencies", extras[name])
        expanded, group_problems = acquisition.resolve_dependency_groups(
            dependency_inputs.get("groups") or {})
        problems.extend(group_problems)
        for name in sorted(expanded):
            add_many(f"pyproject.toml:[dependency-groups].{name}",
                     "dependency_group", expanded[name])

    if project_path is not None:
        pyproject = project_path / "pyproject.toml"
        if pyproject.is_file():
            sha = _sha256_file(pyproject)
            sources.append({"kind": "pyproject", "name": "pyproject.toml",
                            "sha256": sha})
            try:
                data = acquisition._read_pyproject(project_path)
            except Exception as exc:  # noqa: BLE001 - reported, never guessed
                problems.append({"code": "pyproject_unreadable", "scope": "blocking",
                                 "detail": f"pyproject.toml: {exc}"})
                data = {}
            for code in acquisition.dependency_metadata_status(data):
                if code.startswith("dynamic_"):
                    problems.append({
                        "code": "dynamic_declaration_unsupported",
                        "scope": "blocking",
                        "detail": f"pyproject.toml:{code}",
                    })
            add_many("pyproject.toml:[project].dependencies",
                     "project_dependencies",
                     acquisition.runtime_dependencies_from_pyproject(data), sha)
            project_table = data.get("project")
            if isinstance(project_table, Mapping):
                extras = project_table.get("optional-dependencies") or {}
                for name in sorted(extras):
                    add_many(
                        f"pyproject.toml:[project.optional-dependencies].{name}",
                        "optional_dependencies", extras[name], sha)
            expanded, group_problems = acquisition.resolve_dependency_groups(
                data.get("dependency-groups") or {})
            problems.extend(group_problems)
            for name in sorted(expanded):
                add_many(f"pyproject.toml:[dependency-groups].{name}",
                         "dependency_group", expanded[name], sha)

            poetry_groups, poetry_problems = acquisition.poetry_group_dependencies(
                project_path)
            problems.extend(poetry_problems)
            for name, entries in poetry_groups.items():
                add_many(f"pyproject.toml:[tool.poetry.group.{name}].dependencies",
                         "poetry_group", entries, sha)
            poetry_extras, extra_problems = acquisition.poetry_extra_dependencies(
                project_path)
            problems.extend(extra_problems)
            for name, entries in poetry_extras.items():
                add_many(f"pyproject.toml:[tool.poetry.extras].{name}",
                         "poetry_extra", entries, sha)
            hatch_envs, hatch_problems = acquisition.hatch_env_dependencies(project_path)
            problems.extend(hatch_problems)
            for name, entries in hatch_envs.items():
                add_many(f"pyproject.toml:[tool.hatch.envs.{name}]",
                         "hatch_env", entries, sha)

        for name, reader, kind in (
            ("setup.cfg", _setup_cfg_declarations, "setup_cfg"),
            ("setup.py", _setup_py_declarations, "setup_py"),
            ("tox.ini", _tox_declarations, "tox_ini"),
        ):
            path = project_path / name
            if not path.is_file():
                continue
            sha = _sha256_file(path)
            sources.append({"kind": kind, "name": name, "sha256": sha})
            found, file_problems = reader(path)
            problems.extend(file_problems)
            for source, entry_kind, raw in found:
                add(source, entry_kind, raw, sha)

        # requirements files: root-level and a ``requirements/`` directory.
        candidates = {
            path.resolve() for path in project_path.glob("*requirements*.txt")
            if path.is_file()
        }
        requirements_dir = project_path / "requirements"
        if requirements_dir.is_dir():
            candidates |= {
                path.resolve() for path in requirements_dir.glob("*.txt")
                if path.is_file()
            }
        for path in sorted(candidates):
            expanded = acquisition.expand_requirements_file(path, root=project_path)
            problems.extend(expanded["problems"])
            sha = _sha256_file(path)
            sources.append({"kind": "requirements_file", "name": path.name,
                            "sha256": sha})
            for raw in expanded["requirements"]:
                # The source label is the FILE NAME, which is the identity the
                # manifest has always recorded for a requirements declaration.
                add(path.name, "requirements_file", raw, sha)
            for constraint in expanded["constraints"]:
                constraint_path = Path(constraint)
                nested = acquisition.expand_requirements_file(
                    constraint_path, root=project_path)
                problems.extend(nested["problems"])
                nested_sha = _sha256_file(constraint_path)
                sources.append({"kind": "constraints_file",
                                "name": constraint_path.name, "sha256": nested_sha})
                for raw in nested["requirements"]:
                    add(f"constraints:{constraint_path.name}",
                        "constraints_file", raw, nested_sha)

        lock = _lockfile_record(project_path)

    # De-duplicate identical declarations (a project can be read through both
    # ``dependency_inputs`` and its own pyproject).  Identity includes the
    # source, so two files that declare the same thing both stay visible.
    seen: set[tuple[str, str, str]] = set()
    unique: list[dict[str, str]] = []
    for declaration in declarations:
        key = (declaration["source"], declaration["kind"], declaration["raw"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(declaration)

    return {"declarations": unique, "sources": sources,
            "problems": problems, "lock": lock}


def find_explicit_project_pytest_pins(
    project: Path | None = None,
    dependency_inputs: Mapping[str, Any] | None = None,
    test_requirements: Sequence[str] | None = None,
) -> list[tuple[str, str]]:
    """``[(source, raw requirement)]`` for every pytest declaration -- compatibility.

    Retained because callers outside this module used it; it now delegates to the
    complete collector instead of the previous test-only source subset.
    """
    collected = collect_pytest_declarations(
        project=project, dependency_inputs=dependency_inputs,
        test_requirements=test_requirements)
    return [(d["source"], d["raw"]) for d in collected["declarations"]]


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------
_MISSING = object()


def _as_minor(value: Any) -> tuple[int, int] | None:
    if value is None:
        return None
    if isinstance(value, (tuple, list)) and len(value) == 2:
        try:
            return (int(value[0]), int(value[1]))
        except (TypeError, ValueError):
            return None
    if isinstance(value, int) and not isinstance(value, bool):
        return (3, value)
    if not isinstance(value, str):
        # An unrecognised object is NOT stringified: its repr could contain
        # digits that would be mistaken for a version.
        return None
    text = value.strip()
    numbers = re.findall(r"\d+", text)
    if len(numbers) >= 2:
        return (int(numbers[0]), int(numbers[1]))
    return None


def _marker_environment(py_minor: tuple[int, int]) -> dict[str, str]:
    environment = dict(default_environment())
    environment["python_version"] = f"{py_minor[0]}.{py_minor[1]}"
    environment["python_full_version"] = f"{py_minor[0]}.{py_minor[1]}.0"
    return environment


def _conflict(base: Mapping[str, Any], detail: str, *,
              status: str = "CONFLICT", **extra: Any) -> dict[str, Any]:
    out = dict(base)
    out.update(status=status, detail=detail)
    out.update(extra)
    return out


def _valid(base: Mapping[str, Any], *, version: str, pin: str, rule: str,
           source: str, **extra: Any) -> dict[str, Any]:
    out = dict(base)
    out.update(
        status="VALID", detail=None, version=version, pin=pin, rule=rule,
        source=source,
        requires_python=PYTEST_RELEASE_METADATA[version]["requires_python"],
    )
    out.update(extra)
    return out


def select_pytest_version(
    project: Path | None = None,
    declared_python_minor: Any = _MISSING,
    dependency_inputs: Mapping[str, Any] | None = None,
    adjustment: Mapping[str, Any] | None = None,
    test_requirements: Sequence[str] | None = None,
    *,
    runtime_dependencies: Sequence[str] = (),
    admissibility: PytestAdmissibility | None = None,
    require_owner_ratification: bool = False,
    marker_environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Resolve ONE pytest version from every declared constraint, or fail closed.

    Precedence:

    ``RULE_A_*``  the project (or its governing lockfile) constrains pytest;
                  the chosen release is the ratified, technically compatible
                  release satisfying the INTERSECTION of every declaration.
    ``RULE_B_*``  nothing constrains pytest and the default release is ratified
                  and compatible with the declared minor.
    ``RULE_C_*``  nothing constrains pytest and the declared minor is below the
                  default's floor; the fixed fallback table applies.

    A VALID result ALWAYS carries a concrete ``version`` and a ``pin`` of the
    form ``pytest==X.Y.Z``.  There is no path that returns an unpinned install.
    """
    # ---- interpreter minor: explicit, never defaulted --------------------
    if declared_python_minor is _MISSING:
        return {
            "status": "CONFLICT",
            "detail": ("declared Python minor is required: the policy does not "
                       "assume a default interpreter"),
            "rule": "RULE_NONE", "pin": "", "version": None, "source": "",
            "requires_python": "", "declared_python_minor": None,
            "declarations": [], "declaration_sources": [], "problems": [],
            "intersection": "", "candidates": [], "lock": None,
            "admissibility": resolve_pytest_admissibility(admissibility).as_dict(),
            "metadata_snapshot": {"id": METADATA_SNAPSHOT_ID,
                                  "digest": METADATA_PROVENANCE_DIGEST,
                                  "provenance": "NOT ESTABLISHED"},
        }
    py_minor = _as_minor(declared_python_minor)
    if py_minor is None:
        return {
            "status": "CONFLICT",
            "detail": (f"declared Python minor is missing or unparseable: "
                       f"{declared_python_minor!r}"),
            "rule": "RULE_NONE", "pin": "", "version": None, "source": "",
            "requires_python": "", "declared_python_minor": None,
            "declarations": [], "declaration_sources": [], "problems": [],
            "intersection": "", "candidates": [], "lock": None,
            "admissibility": resolve_pytest_admissibility(admissibility).as_dict(),
            "metadata_snapshot": {"id": METADATA_SNAPSHOT_ID,
                                  "digest": METADATA_PROVENANCE_DIGEST,
                                  "provenance": "NOT ESTABLISHED"},
        }

    policy = resolve_pytest_admissibility(admissibility)
    collected = collect_pytest_declarations(
        project=project, dependency_inputs=dependency_inputs,
        runtime_dependencies=runtime_dependencies,
        test_requirements=test_requirements, adjustment=adjustment)

    base: dict[str, Any] = {
        "rule": "RULE_NONE",
        "pin": "",
        "version": None,
        "source": "",
        "requires_python": "",
        "status": "CONFLICT",
        "detail": None,
        "declared_python_minor": [py_minor[0], py_minor[1]],
        "declarations": collected["declarations"],
        "declaration_sources": collected["sources"],
        "problems": collected["problems"],
        "intersection": "",
        "candidates": [],
        "lock": collected["lock"],
        "admissibility": policy.as_dict(),
        "metadata_snapshot": {
            "id": METADATA_SNAPSHOT_ID,
            "digest": METADATA_PROVENANCE_DIGEST,
            "provenance": "NOT ESTABLISHED",
            "source_url": "https://pypi.org/pypi/pytest/json",
        },
        "python_environment": {
            "minor": f"{py_minor[0]}.{py_minor[1]}",
            "platform": (marker_environment or _marker_environment(py_minor)).get(
                "sys_platform", ""),
        },
    }

    if require_owner_ratification and not policy.owner_ratified:
        return _conflict(
            base,
            "PYTEST_ADMISSIBILITY_NOT_RATIFIED: no owner-ratified pytest release "
            "set is recorded (decision D1); refusing to resolve under an "
            "unratified policy",
            status="ADMISSIBILITY_NOT_RATIFIED",
        )

    blocking = [p for p in collected["problems"] if p.get("scope") == "blocking"]
    if blocking:
        return _conflict(
            base,
            "pytest declaration sources cannot be evaluated statically: "
            + "; ".join(f"{p.get('code')}:{p.get('detail')}" for p in blocking),
        )

    environment = dict(marker_environment or _marker_environment(py_minor))

    # ---- parse every declaration ----------------------------------------
    parsed: list[dict[str, Any]] = []
    for declaration in collected["declarations"]:
        raw = declaration["raw"]
        try:
            requirement = Requirement(raw)
        except InvalidRequirement as exc:
            return _conflict(
                base, f"unparsable project pytest pin {raw!r}: {exc}")
        if canonicalize_name(requirement.name) != "pytest":
            continue
        if requirement.url:
            return _conflict(
                base,
                f"unsupported direct reference for pytest in "
                f"{declaration['source']}: {raw!r}")
        if any(spec.operator == "===" for spec in requirement.specifier):
            return _conflict(
                base,
                f"unsupported arbitrary equality (===) for pytest in "
                f"{declaration['source']}: {raw!r}")
        applies = True
        if requirement.marker is not None:
            try:
                applies = bool(requirement.marker.evaluate(environment))
            except Exception as exc:  # noqa: BLE001 - unknown marker is blocking
                return _conflict(
                    base,
                    f"unsupported environment marker for pytest in "
                    f"{declaration['source']}: {raw!r} ({exc})")
        parsed.append({**declaration, "specifier": str(requirement.specifier),
                       "applies": applies})

    specifier_text = sorted({
        spec
        for declaration in parsed if declaration["applies"]
        for spec in declaration["specifier"].split(",") if spec
    })
    intersection = SpecifierSet(",".join(specifier_text))
    base["intersection"] = str(intersection)

    def technically_compatible(version: str) -> bool:
        return py_minor in PYTEST_RELEASE_METADATA[version]["admitted_minors"]

    def ratified(version: str) -> bool:
        return version in policy.versions

    candidates = sorted(
        [version for version in PYTEST_RELEASE_METADATA
         # Pre-releases are never selected: the policy admits final releases
         # only unless a governing document says otherwise, and none does.
         if not Version(version).is_prerelease
         and ratified(version) and technically_compatible(version)
         and intersection.contains(version, prereleases=False)],
        key=Version)
    base["candidates"] = candidates

    exact_versions = sorted({
        spec.version
        for declaration in parsed if declaration["applies"]
        for spec in SpecifierSet(declaration["specifier"])
        if spec.operator == "==" and not spec.version.endswith("*")
    })

    # ---- a governing lockfile is the resolution, if it is admissible -----
    lock = collected["lock"]
    if lock and lock.get("found"):
        versions = [str(v) for v in (lock.get("versions") or []) if str(v)]
        if lock.get("error") and not versions:
            return _conflict(
                base, f"lockfile {lock.get('file')} does not resolve pytest: "
                      f"{lock.get('error')}")
        if len(versions) > 1:
            return _conflict(
                base, f"lockfile {lock.get('file')} records multiple pytest "
                      f"versions: {versions}")
        if not versions:
            return _conflict(
                base, f"lockfile {lock.get('file')} names pytest without a "
                      "resolvable version")
        locked = versions[0]
        if locked not in PYTEST_RELEASE_METADATA:
            return _conflict(
                base, f"lockfile {lock.get('file')} resolves pytest {locked}, "
                      "which has no fixed release metadata")
        if not ratified(locked):
            return _conflict(
                base, f"lockfile {lock.get('file')} resolves pytest {locked}, "
                      f"which is outside the ratified set {list(policy.versions)}")
        if not technically_compatible(locked):
            return _conflict(
                base, f"lockfile resolves pytest {locked} which requires "
                      f"{PYTEST_RELEASE_METADATA[locked]['requires_python']} and "
                      f"rejects Python {py_minor}")
        if not intersection.contains(locked):
            return _conflict(
                base, f"lockfile resolution pytest {locked} violates the "
                      f"declared constraint {intersection or '<none>'}")
        return _valid(base, version=locked, pin=f"pytest=={locked}",
                      rule="RULE_A_LOCKFILE_RESOLUTION",
                      source=f"{lock.get('file')}")

    # ---- nothing constrains pytest: default, then compatibility table ----
    if not specifier_text:
        default_version = DEFAULT_PYTEST_PIN.split("==", 1)[1]
        if ratified(default_version) and technically_compatible(default_version):
            return _valid(base, version=default_version, pin=DEFAULT_PYTEST_PIN,
                          rule="RULE_B_DEFAULT_PIN", source="default")
        fallback_pin = PYTEST_COMPATIBILITY_FALLBACK.get(py_minor)
        if fallback_pin:
            fallback_version = fallback_pin.split("==", 1)[1]
            if ratified(fallback_version) and technically_compatible(fallback_version):
                return _valid(
                    base, version=fallback_version, pin=fallback_pin,
                    rule="RULE_C_PYTHON_COMPATIBILITY_FALLBACK",
                    source=f"PYTEST_COMPATIBILITY_FALLBACK[{py_minor}]")
            return _conflict(
                base, f"compatibility fallback {fallback_pin} for Python "
                      f"{py_minor} is not ratified or not technically compatible")
        return _conflict(
            base, f"no compatibility fallback available for Python minor {py_minor}")

    # ---- constrained: the intersection must admit a ratified release -----
    if not candidates:
        trace = ", ".join(
            f"{d['source']}:{d['raw']}" for d in parsed if d["applies"])
        if len(exact_versions) > 1:
            return _conflict(
                base, f"conflicting project pytest pins: {exact_versions} "
                      f"(declarations: {trace})")
        if len(exact_versions) == 1:
            version = exact_versions[0]
            if version not in PYTEST_RELEASE_METADATA:
                return _conflict(
                    base, f"missing or unverified metadata for pytest version {version}")
            if not ratified(version):
                return _conflict(
                    base, f"project pin pytest=={version} is outside the "
                          f"ratified set {list(policy.versions)}")
            if not technically_compatible(version):
                return _conflict(
                    base, f"project pin pytest=={version} requires "
                          f"{PYTEST_RELEASE_METADATA[version]['requires_python']} "
                          f"which rejects Python {py_minor}")
            # The pin is admissible in itself, so the empty intersection comes
            # from the project's OTHER declarations excluding it.
            return _conflict(
                base,
                f"project pin pytest=={version} contradicts the declared "
                f"constraint {intersection}")
        return _conflict(
            base, f"no ratified pytest release satisfies {intersection} for "
                  f"Python {py_minor} (declarations: {trace})")

    if len(exact_versions) == 1:
        chosen = exact_versions[0]
        if chosen not in candidates:
            # The project pinned a release its OWN other declarations exclude
            # (e.g. ``pytest==8.4.1`` plus ``pytest!=8.4.1``).  Choosing either
            # side silently would violate one of them.
            return _conflict(
                base,
                f"project pin pytest=={chosen} contradicts the declared "
                f"constraint {intersection}")
        rule = "RULE_A_EXPLICIT_PROJECT_PIN"
        source = next(
            (d["source"] for d in parsed
             if d["applies"] and d["specifier"] and
             f"=={chosen}" in d["specifier"]), "declared")
    else:
        chosen = candidates[-1]
        rule = "RULE_A_DECLARED_CONSTRAINT"
        source = next(
            (d["source"] for d in parsed
             if d["applies"] and d["specifier"] and
             intersection.contains(chosen)), "declared")
    return _valid(base, version=chosen, pin=f"pytest=={chosen}", rule=rule,
                  source=source)
