"""F2 S8 pilot: host-side workspace builder (acquisition/build boundary).

The S8 package splits execution into two boundaries. This script is the FIRST
one: it has network access and resolves each pinned task's repository,
interpreter environment and dependency set. Nothing here runs a task's tests
and nothing here produces scientific evidence -- that happens offline in the
guest, under the governed pipeline.

Workspaces are built under a FIXED absolute root (``C:\\f2_pilot``) that is
reproduced identically inside the guest, because a virtual environment embeds
absolute paths in its launcher scripts and console entry points. Building at
one path and evaluating at another silently breaks every installed console
script, which would look like a task failure rather than a delivery defect.

Each workspace is: repo at base_commit + test_patch (the base state), plus a
gold sibling that additionally carries the gold patch. Both are built here so
the guest never needs git, a compiler or the network.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from packaging.version import Version

from qwen_train.f2_population_build import source_to_screen_row
from qwen_train.f2_evaluator import analyze_test_invocation
from qwen_train.f2_s8_acquisition import (
    BUILDER_SOURCE,
    DECLARED_ADJUSTMENTS,
    DEFAULT_PYTEST_PIN,
    INTERPRETER_SEARCH_ROOTS,
    PREFERRED_PYTHON_MINORS,
    PytestPolicyError,
    builder_install_plan,
    declared_required_version,
    declared_test_requirements,
    discover_interpreters,
    project_install_context,
    repo_url_for,
    require_ratified_pytest_admissibility,
    require_unprivileged_identity,
    requires_python,
    resolve_task_project_root,
    select_pytest_version,
)

#: Git executable for the acquisition host. The builder is a Windows-host tool
#: and the pilot evidence was produced with the exact binary below, so Windows
#: keeps that literal path UNCHANGED -- substituting a different git could alter
#: a governed build. On every other platform the literal does not exist, so it is
#: resolved from PATH instead. The literal alone made the Linux CI runner fail
#: every plugin-compatibility test with ``FileNotFoundError`` (11 CI failures);
#: git is on PATH there, so the resolution succeeds.
GIT = (
    r"C:\Program Files\Git\cmd\git.exe"
    if os.name == "nt"
    else (shutil.which("git") or "git")
)
INPUTS = Path(r"C:\Users\rober\F2_EVIDENCE\s8\pilot_inputs\full")
PATCHES = Path(r"C:\Users\rober\F2_EVIDENCE\s8\pilot_build\patches")
HOST_PYTHON = r"C:\Users\rober\Projects\v-horseshoe-v2\.venv\Scripts\python.exe"
ROOT = Path(r"C:\f2_pilot")


def workspace_path(root: Path | str | None, task_id: str) -> Path:
    """The task workspace under ``root`` (default :data:`ROOT`).

    The builder writes into ``<root>/<task_id>``; the *only* place that rule
    lives.  Callers that own a different workspace root (a rehearsal, a test)
    pass it explicitly instead of relying on the fixed pilot path.
    """
    return Path(root if root is not None else ROOT) / task_id

#: Interpreters available on the acquisition host, in PREFERENCE order.
#:
#: Order is a deliberate policy, not an accident of installation. Newer is not
#: better: a task is built on the first interpreter that satisfies its declared
#: ``requires-python``, and wheel coverage collapses at the newest releases. The
#: measured case is ``sissbruecker__linkding-1175`` (``>=3.13``), where uv picked
#: the newly-installed CPython 3.15 and ``psycopg-binary`` had no wheel for it.
#:
#: 3.12 and 3.13 lead because they have the widest prebuilt-wheel coverage for
#: 2024-2025 dependencies; 3.11 then 3.14 follow; 3.10 is the last resort for
#: projects that still exclude 3.14. Entries that are not installed are skipped
#: by ``pick_interpreter``, so listing a future version is harmless.
#:
#: CPython 3.15 is deliberately ABSENT: it is present on this host and is the
#: py-launcher default, so leaving it out is what stops it being selected.
INTERPRETERS = (
    r"C:\Users\rober\AppData\Local\Programs\Python\Python312\python.exe",  # 3.12
    r"C:\Users\rober\AppData\Local\Programs\Python\Python313\python.exe",  # 3.13
    r"C:\Users\rober\AppData\Local\Programs\Python\Python311\python.exe",  # 3.11
    r"C:\Python314\python.exe",                      # 3.14.8
    r"C:\Users\rober\AppData\Local\Programs\Python\Python310\python.exe",  # 3.10.11
    HOST_PYTHON,                                     # 3.14.8, repo venv
)

REPOS = {
    "HypothesisWorks__hypothesis-4532": "https://github.com/HypothesisWorks/hypothesis.git",
    "python__mypy-19767": "https://github.com/python/mypy.git",
    "stanfordnlp__dspy-8739": "https://github.com/stanfordnlp/dspy.git",
    "openai__openai-agents-python-1633": "https://github.com/openai/openai-agents-python.git",
    "openai__openai-agents-python-1636": "https://github.com/openai/openai-agents-python.git",
}


def declared_test_cmd(rec: Mapping[str, Any]) -> str:
    """The canonical declared command for a task record -- never a raw field.

    A SWE-bench-Live row declares ``test_cmds`` as a LIST and the frozen
    registration binds the canonical chain the source adapter derives from it
    (see ``f2_s8_pilot_live.build_specs``); reading the raw list here would
    describe a different command than the one the registration bound.
    """
    return str(source_to_screen_row("swe-bench-live", rec).get("test_cmd") or "")


def uses_uv_environment(test_cmd: str) -> bool:
    """True when the DECLARED command is driven by ``uv``.

    Such a task's environment is owned by uv, which creates ``repo/.venv``
    itself; building a second, unused pip venv for it only doubles the delivered
    payload (see the ``uses_uv`` comment in :func:`build`).  Derived from the
    command rather than a hand-maintained list, so a task added later is handled
    by the same rule as the pilot's two uv tasks (their declared commands are
    ``uv run pytest -rA``).

    The shared analyser is used so the builder, the pre-check and the live runner
    can never disagree about whether a command is uv-driven.
    """
    invocation = analyze_test_invocation(str(test_cmd or ""))
    if invocation.ok:
        return invocation.uses_uv
    return test_cmd.strip().startswith("uv ")


def resolve_repo_url(task_id: str, inputs: Path) -> str:
    """The clone URL for a task: the explicit pilot mapping, else its own record.

    Returns ``""`` when neither exists, which is the caller's signal to skip the
    task -- exactly the behaviour of the hand-maintained mapping.  A record whose
    ``repo`` is not ``owner/name`` raises (:func:`repo_url_for`), so a malformed
    record can never be silently turned into a plausible URL.
    """
    explicit = REPOS.get(task_id)
    if explicit:
        return explicit
    rec_path = Path(inputs) / f"{task_id}.json"
    if not rec_path.is_file():
        return ""
    rec = json.loads(rec_path.read_text(encoding="utf-8"))
    return repo_url_for(str(rec.get("repo") or ""))

#: pytest pins. hypothesis at this commit reads ``Argument._long_opts``, which
#: pytest 9 removed, so its own conftest cannot even load under 9. Pinning to a
#: version whose private API it understands is an environment decision, not a
#: change to the declared test command.
#:
#: Deterministic precedence: Rule A (project/adjustment pin) -> Rule B (default 8.3.5)
#: -> Rule C (compatibility fallback from fixed metadata table).
PYTEST_PIN = DEFAULT_PYTEST_PIN

#: Dedicated builder-owned tools directory and approved cryptographic digests.
#: The builder account (f2builder) must use this directory rather than depending
#: on unauthorized ACL grants in the owner's personal profile (C:\Users\rober\.local\bin).
DEFAULT_UV_PATH = Path(r"C:\f2_pilot\tools\uv.exe")
UV_APPROVED_SHA256 = "92fcafc6f7512ec3a0fb42bf6bf84730737252c6d3d7cae4f6f97678d746da3a"


def _is_ephemeral_fixture_path(p_posix: str) -> bool:
    """True for the ephemeral directories a TEST creates under the owner profile.

    The profile-isolation guard exists so ``f2builder`` never depends on ACLs in
    the owner's personal profile.  A pytest temporary directory is not such a
    dependency: pytest creates it for the test process and removes it
    afterwards.  The previous marker list (``pytest_tmp``) never matched
    pytest's real layout (``.../pytest-of-<user>/pytest-<n>/...``), so every
    Windows run of the uv-path tests tripped the guard instead of exercising the
    digest check it was testing.

    The exemption is deliberately narrow: named fixture roots plus pytest's own
    two temp-directory naming schemes.  A durable profile path -- including the
    owner's ``.local/bin`` -- still raises.
    """
    for part in p_posix.split("/"):
        if part in ("f2_evidence", "f2_scratch", "pytest_tmp"):
            return True
        if part.startswith("pytest-of-"):
            return True
        if re.fullmatch(r"pytest-\d+", part):
            return True
    return False


def resolve_and_verify_uv(
    custom_path: Path | str | None = None,
    expected_sha256: str | None = None,
) -> Path:
    r"""Resolve and verify the dedicated, builder-owned uv executable. Deterministic.

    Enforces:
    1. Dedicated builder-owned tools directory (default C:\f2_pilot\tools\uv.exe,
       or configured via F2_BUILDER_UV_PATH / custom_path).
    2. Profile isolation guard: fail-closed if the resolved path points into the
       owner profile directory (e.g. C:\Users\rober), preventing dependence on
       unauthorized profile ACLs.
    3. File existence: raises FileNotFoundError ("BUILDER_TOOL_UNAVAILABLE") if absent.
    4. Cryptographic integrity: verifies SHA-256 against UV_APPROVED_SHA256
       before execution; raises RuntimeError ("BUILDER_TOOL_CORRUPT") on mismatch.
    """
    if custom_path is not None:
        p = Path(custom_path)
    elif os.environ.get("F2_BUILDER_UV_PATH"):
        p = Path(os.environ["F2_BUILDER_UV_PATH"])
    else:
        p = DEFAULT_UV_PATH

    # Profile isolation guard: ensure f2builder does not depend on owner profile ACLs
    p_posix = p.as_posix().lower()
    forbidden_prefixes = [
        Path(r"C:\Users\rober"),
        Path.home(),
    ]
    if "USERPROFILE" in os.environ:
        forbidden_prefixes.append(Path(os.environ["USERPROFILE"]))

    for forbidden in forbidden_prefixes:
        f_posix = forbidden.as_posix().lower()
        is_owner_profile = (
            p_posix == f_posix
            or p_posix.startswith(f_posix + "/")
            or "c:/users/rober" in p_posix
        )
        if is_owner_profile and not _is_ephemeral_fixture_path(p_posix):
            raise RuntimeError(
                f"PROFILE_ISOLATION_VIOLATION: uv path {p} is located within user profile {forbidden}; "
                f"f2builder must use dedicated tools directory"
            )

    if not p.is_file():
        raise FileNotFoundError(
            f"BUILDER_TOOL_UNAVAILABLE: uv executable not found at {p}"
        )

    digest = _sha256_file(p)
    expected = (expected_sha256 or UV_APPROVED_SHA256).lower()
    if digest.lower() != expected:
        raise RuntimeError(
            f"BUILDER_TOOL_CORRUPT: uv executable at {p} has digest {digest}, expected {expected}"
        )
    return p

#: POSIX ``tee`` must be resolvable on PATH. ``tests/mcp/helpers.py`` does
#: ``assert shutil.which("tee")`` at import time, and the assertion runs during
#: COLLECTION, so a missing ``tee`` aborts the entire pytest session before a
#: single declared node can run -- turning an environment gap into what would
#: look like a task failure. Git for Windows ships it; only the directory is
#: missing from PATH.
POSIX_BIN = r"C:\Program Files\Git\usr\bin"

#: Optional dependency groups the openai-agents test suite imports unguarded.
#: Without them collection aborts on ``ModuleNotFoundError`` for ``litellm`` and
#: ``numpy``. Resolving them is acquisition work; the declared command is
#: unchanged.
UV_ALL_EXTRAS = ("openai__openai-agents-python-1633",
                 "openai__openai-agents-python-1636")

#: Extra packages a task's OWN suite demands at collection time. Each was found
#: by running the declared command on the acquisition host, which is the phase
#: whose job is exactly this: resolve the environment, so a dependency gap can
#: never be misread later as a task outcome.
#:
#: * mypy's ``pyproject.toml`` sets ``addopts = -nauto``, and pytest rejects an
#:   unknown ``-n`` outright (exit 4, no JUnit at all) without xdist.
#: * dspy imports ``datamodel_code_generator`` and ``PIL`` unguarded in two
#:   test modules, and a collection error aborts the whole session.
EXTRA_DEPS = {
    "python__mypy-19767": ["pytest-xdist"],
    "stanfordnlp__dspy-8739": ["datamodel-code-generator", "pillow"],
}

#: ``addopts`` tokens that imply a pytest PLUGIN distribution must be installed
#: before the declared command can even configure.  A project declares these in
#: its own pytest configuration, not in its dependency list, so
#: ``project_install_context`` cannot see them -- and pytest aborts with exit 4
#: ("unrecognized arguments") or crashes in ``pytest_configure``, producing NO
#: JUnit at all, which is then indistinguishable from a task outcome unless the
#: environment is resolved first (the whole job of this build phase).
#:
#: Keyed by the option's leading token (``-n`` matches ``-n``, ``-n4``,
#: ``-nauto``); ``--cov`` matches every ``--cov*`` flag.  Only options with an
#: unambiguous owning distribution are listed -- nothing is guessed.
_PLUGIN_BY_OPTION: tuple[tuple[str, str], ...] = (
    ("-n", "pytest-xdist"),
    ("--numprocesses", "pytest-xdist"),
    ("--dist", "pytest-xdist"),
    ("--cov", "pytest-cov"),
    ("--benchmark", "pytest-benchmark"),
    ("--timeout", "pytest-timeout"),
    ("--forked", "pytest-forked"),
    ("--randomly", "pytest-randomly"),
    ("--asyncio-mode", "pytest-asyncio"),
    ("--reruns", "pytest-rerunfailures"),
    ("--instafail", "pytest-instafail"),
    ("--html", "pytest-html"),
    ("--xdoctest", "pytest-xdoctest"),
)

_PYTEST_CONFIG_FILES = ("pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini")


def pytest_config_plugins(project: Path) -> list[str]:
    """Distributions a project's OWN pytest ``addopts`` require. Deterministic.

    Reads the same configuration pytest reads, extracts every ``addopts`` value,
    and maps each option token to its owning distribution via
    :data:`_PLUGIN_BY_OPTION`.  Returns a sorted, de-duplicated list; an
    unreadable or absent configuration yields ``[]`` (never an invented
    requirement).
    """
    import re

    tokens: list[str] = []
    for name in _PYTEST_CONFIG_FILES:
        f = project / name
        if not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        values: list[str] = []
        # pyproject `addopts` may be a SINGLE-LINE array, a MULTI-LINE array
        # (each element on its own line -- the form `vacanza__holidays-2859`
        # uses), or a plain quoted/ini string.  All three must parse, or the
        # plugin is silently missed and the declared command aborts with exit 4.
        for m in re.finditer(r"^\s*addopts\s*=\s*(\[.*?\])", text,
                             re.MULTILINE | re.DOTALL):
            values.append(m.group(1))
        for m in re.finditer(r"^\s*addopts\s*=\s*([^\[]\S.*)$", text, re.MULTILINE):
            values.append(m.group(1))
        for value in values:
            for q in re.findall(r"[\"']([^\"']+)[\"']", value):
                tokens.extend(q.split())
            if not re.search(r"[\"']", value):        # bare ini form
                tokens.extend(value.strip("\"'").split())
    def _matches(tok: str, opt: str) -> bool:
        """A token implies ``opt``'s plugin.

        Long options match by PREFIX so the flag's own arguments are covered:
        ``--cov`` must match ``--cov``, ``--cov=src`` and
        ``--cov-fail-under=100`` (the form `vacanza__holidays-2859` uses), and
        ``--randomly`` must match ``--randomly-seed=1``.  The short ``-n`` needs
        its own rule because ``-n``, ``-n4`` and ``-nauto`` share a prefix while
        ``--numprocesses`` is handled by its own long entry.
        """
        if opt == "-n":
            return tok == "-n" or (tok.startswith("-n") and not tok.startswith("--"))
        return tok == opt or tok.startswith(opt)

    found = {
        dist for tok in tokens for opt, dist in _PLUGIN_BY_OPTION
        if _matches(tok, opt)
    }
    return sorted(found)


def command_plugins(test_cmd: str) -> list[str]:
    """Distributions the DECLARED test command itself requires.

    ``pytest_config_plugins`` reads the project's ``addopts``; this reads the
    other place a project declares a pytest option -- its registered command.
    ``BerriAI__litellm-13675`` declares ``poetry run pytest -vv -n 4 -rA
    tests/test_litellm`` and its ``dev`` group contains no xdist, so pytest
    aborted with ``error: unrecognized arguments: -n`` (exit 4, no JUnit). Its
    own CI declares the plugin explicitly -- ``.github/workflows/test-litellm.yml``
    installs ``pytest-xdist``, and so do the ``Makefile`` and CircleCI config --
    so installing it honours the project's declared test environment rather than
    inventing a dependency. A flag with no unambiguous owner yields nothing.
    """
    tokens = str(test_cmd or "").split()

    def _matches(tok: str, opt: str) -> bool:
        if opt == "-n":
            return tok == "-n" or (tok.startswith("-n") and not tok.startswith("--"))
        return tok == opt or tok.startswith(opt)

    return sorted({
        dist for tok in tokens for opt, dist in _PLUGIN_BY_OPTION
        if _matches(tok, opt)
    })


def effective_pytest_pin(
    adjustment: Mapping[str, Any],
    project: Path | None = None,
    declared_python_minor: Any = None,
    *,
    runtime_dependencies: Sequence[str] = (),
) -> str:
    """The CONCRETE pytest requirement the installer will use for one task.

    Delegates to :func:`select_pytest_version` and therefore inherits its
    fail-closed contract: a range-only declaration resolves to a specific
    release, and a missing interpreter minor is refused rather than defaulted.
    An unresolved result can never come back from here.
    """
    sel = select_pytest_version(
        project=project,
        declared_python_minor=declared_python_minor,
        adjustment=adjustment,
        runtime_dependencies=runtime_dependencies,
    )
    if sel.get("status") != "VALID" or not sel.get("version"):
        raise RuntimeError(f"PROJECT_PIN_CONFLICT: {sel.get('detail')}")
    return approved_pytest_requirement(sel)


def approved_pytest_requirement(selection: Mapping[str, Any]) -> str:
    """The exact ``pytest==X.Y.Z`` requirement a VALID selection authorises.

    A selection without a concrete version is NOT a pin: returning one would
    let pip install whatever it liked while the manifest claimed a policy.
    """
    version = str(selection.get("version") or "").strip()
    if not version:
        raise RuntimeError(
            "PYTEST_PIN_UNRESOLVED: the selection carries no concrete pytest "
            f"version (status={selection.get('status')!r}); refusing to install "
            "an unpinned pytest")
    return f"pytest=={version}"


def installed_pytest_version(python: str | Path, *, timeout: int = 300) -> str:
    """The version of the pytest distribution INSTALLED in ``python``'s env.

    Read through ``importlib.metadata`` (the installed-distribution database),
    not by parsing ``pip list`` text and not from the requirement that was
    requested: a plugin or the project itself can upgrade pytest after the
    approved pin was installed, and only the installed metadata shows that.
    """
    code = (
        "import importlib.metadata as m\n"
        "try:\n"
        "    print(m.version('pytest'))\n"
        "except m.PackageNotFoundError:\n"
        "    raise SystemExit(3)\n"
    )
    proc = subprocess.run([str(python), "-c", code],
                          capture_output=True, text=True, timeout=timeout)
    if proc.returncode == 3:
        raise RuntimeError(
            f"PYTEST_INSTALLED_UNVERIFIABLE: pytest is not installed in {python}")
    if proc.returncode != 0:
        raise RuntimeError(
            f"PYTEST_INSTALLED_UNVERIFIABLE: cannot read installed pytest from "
            f"{python} (rc={proc.returncode}): {proc.stderr[-500:]}")
    return proc.stdout.strip()


def assert_installed_pytest(
    python: str | Path, approved_version: str, *,
    label: str = "",
) -> dict[str, Any]:
    """Fail closed unless the INSTALLED pytest is the approved release.

    Returns the evidence record written into the manifest.  ``tests`` exercise
    the failure paths; the build calls this after every installation phase.
    """
    if not str(approved_version or "").strip():
        raise RuntimeError("PYTEST_INSTALLED_UNVERIFIABLE: no approved version")
    installed = installed_pytest_version(python)
    same = Version(installed) == Version(approved_version)
    evidence = {
        "label": label,
        "approved_version": str(approved_version),
        "installed_version": installed,
        "source": "importlib.metadata",
        "matches": same,
    }
    if not same:
        # The measured drift case: awslabs__mcp-1154 asked for pytest 8.3.5 and
        # pytest 9.1.1 was observably installed afterwards.
        raise RuntimeError(
            f"PYTEST_INSTALLED_DRIFT: approved pytest {approved_version} but "
            f"{installed} is installed in {python}"
            + (f" ({label})" if label else ""))
    return evidence


def run(cmd, *, cwd=None, timeout=3600, check=True):
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"command failed rc={proc.returncode}: {' '.join(map(str, cmd))}\n"
            f"stdout: {proc.stdout[-2000:]}\nstderr: {proc.stderr[-2000:]}"
        )
    return proc


def _version_of(python: str) -> tuple[int, int]:
    out = run([python, "-c", "import sys;print(sys.version_info[0],sys.version_info[1])"]).stdout
    major, minor = out.split()
    return int(major), int(minor)


def _requires_python(project: Path) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """Declared bounds, delegated to the ONE parser in ``f2_s8_acquisition``.

    This used to be a second, weaker regex that matched only double-quoted
    assignments. ``requires-python = '>=3.8,<3.14'`` (single quotes) therefore
    looked unconstrained, the newest interpreter was chosen, and pip rejected the
    package mid-install -- which is exactly how the SDV tasks failed.
    """
    return requires_python(Path(project))


_DISCOVERED_INTERPRETERS: dict[tuple[tuple[int, int], ...] | None, list[str]] = {}

#: OWNER-RATIFIED environment sidecar.  Read-only input; the builder never
#: writes it.  Only records with status ``ratified`` are usable.  ``proposed`` is
#: explicitly NOT accepted: correct hashes are an integrity property, not an
#: authorization.
#:
#: The 2026-10-22 revision supersedes the 2026-10-20 file: it carries the same
#: 11 declarations verbatim and adds 12 declaration-derived ones (8 built tasks
#: keep the minor their interpreter reports; 4 proposed tasks use their own
#: ``requires-python`` lower bound).  The older file and its provenance are left
#: untouched in F2_SCRATCH; this sidecar's provenance binds it as the superseded
#: source.
DECLARED_ENVIRONMENTS_SIDECAR = Path(
    os.environ.get("F2_S8_DECLARED_ENVIRONMENTS_SIDECAR")
    or r"C:\Users\rober\F2_EVIDENCE\s8\declared_environments\declared_environments_ratified_20261022.json"
)
DECLARED_ENVIRONMENTS_PROVENANCE = Path(
    os.environ.get("F2_S8_DECLARED_ENVIRONMENTS_PROVENANCE")
    or r"C:\Users\rober\F2_EVIDENCE\s8\declared_environments\declared_environments_ratified_provenance_20261022.json"
)
_SIDECAR_SCHEMA = "f2-s8-declared-environments/v2"
_PROVENANCE_SCHEMA = "f2-s8-declared-environments-provenance/v2"
_EVIDENCE_SCHEMA = "f2-s8-live-image-map/v1"
#: Declaration-derived evidence for tasks that have NO container image.  The
#: record states, per task, the declared ``requires-python`` and the interpreter
#: that satisfies it -- facts read from the project tree, not an image config.
EVIDENCE_SCHEMA_DECLARATION = "f2-s8-declared-environment-evidence/v2"
_POLICY_EVIDENCE_SCHEMAS = (_EVIDENCE_SCHEMA, EVIDENCE_SCHEMA_DECLARATION)
_OLD_SIDECAR_SCHEMA = "f2-s8-declared-environments/v1"
#: A supersession chain may start from either prior version of the sidecar.
_OLD_SIDECAR_SCHEMAS = (_OLD_SIDECAR_SCHEMA, _SIDECAR_SCHEMA)
_SIDECAR_RATIFIED_STATUS = "ratified"
_SIDECAR_BLOCKED_STATUS = "blocked_conflict"
_SIDECAR_REJECTED_STATUSES = ("proposed",)


class DeclaredInterpreterError(RuntimeError):
    """A task's DECLARED Python minor is missing, ambiguous or unsatisfiable."""


class DeclaredEnvironmentError(RuntimeError):
    """The declared-environment sidecar is untrustworthy or inconsistent."""


def declared_minor_tuple(declared: str | int | None) -> tuple[int, int] | None:
    """(major, minor) from the evidence forms used by the task records.

    Accepts ``python_base_310``, ``3.10``, ``3.10.18`` and ``310``.  Returns
    ``None`` when nothing parseable is present -- callers MUST fail closed.
    """
    if declared is None:
        return None
    text = str(declared).strip()
    if not text:
        return None
    m = re.search(r"python_base_3(\d+)", text)
    if m:
        return (3, int(m.group(1)))
    nums = re.findall(r"\d+", text)
    if len(nums) >= 2:
        return (int(nums[0]), int(nums[1]))
    if len(nums) == 1 and len(nums[0]) == 3 and nums[0].startswith("3"):
        return (3, int(nums[0][1:]))
    return None


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _strict_json(text: str, what: str):
    """json.loads that refuses duplicate keys (no silent last-wins)."""

    def _hook(pairs):
        seen = set()
        for k, _ in pairs:
            if k in seen:
                raise DeclaredEnvironmentError(f"{what}: duplicate key {k!r}")
            seen.add(k)
        return dict(pairs)

    try:
        return json.loads(text, object_pairs_hook=_hook)
    except DeclaredEnvironmentError:
        raise
    except ValueError as exc:
        raise DeclaredEnvironmentError(f"{what}: malformed JSON: {exc}") from exc


def _check_bound_any(defn: dict, key: str, expected_schemas: Sequence[str],
                     what: str) -> Path:
    """Validate one {path, sha256, schema} binding against ACCEPTED schemas.

    The evidence model has two legitimate shapes: the image-derived map
    (``f2-s8-live-image-map/v1``) and the declaration-derived record
    (``f2-s8-declared-environment-evidence/v2``) used for tasks that have no
    container image.  A supersession chain may also start from either version of
    the previous sidecar, so both are accepted -- but nothing else is.
    """
    rec = defn.get(key)
    if not isinstance(rec, dict):
        raise DeclaredEnvironmentError(f"provenance.{key} is not an object")
    p = Path(str(rec.get("path", "")))
    if not p.is_file():
        raise DeclaredEnvironmentError(f"{what} missing: {p}")
    if _sha256_file(p) != rec.get("sha256"):
        raise DeclaredEnvironmentError(f"{what} digest mismatch: {p}")
    if rec.get("schema") not in tuple(expected_schemas):
        raise DeclaredEnvironmentError(
            f"{what} schema {rec.get('schema')!r} not in {tuple(expected_schemas)!r}")
    return p


def load_declared_environments(
    sidecar: Path | str | None = None,
    provenance: Path | str | None = None,
) -> dict[str, str]:
    """Validated {task_id: minor} from the RATIFIED sidecar, or raise.

    Integrity is bound to the ACTUAL bytes of the sidecar, of the previous
    ratified sidecar it supersedes, and of the evidence the declarations rest on
    (an image map, or the declaration-derived record for tasks with no image),
    all via the provenance record.  Authorization is separate: only ``ratified``
    records are usable, whatever their hashes say.
    """
    sp = Path(sidecar or DECLARED_ENVIRONMENTS_SIDECAR)
    pv = Path(provenance or DECLARED_ENVIRONMENTS_PROVENANCE)
    if not sp.is_file():
        raise DeclaredEnvironmentError(f"sidecar missing: {sp}")
    if not pv.is_file():
        raise DeclaredEnvironmentError(f"provenance missing: {pv}")

    prov = _strict_json(pv.read_text(encoding="utf-8"), "provenance")
    if prov.get("schema") != _PROVENANCE_SCHEMA:
        raise DeclaredEnvironmentError(
            f"provenance schema {prov.get('schema')!r} != {_PROVENANCE_SCHEMA!r}")
    if prov.get("ratified_by") != "Rob":
        raise DeclaredEnvironmentError(
            f"provenance ratifying authority {prov.get('ratified_by')!r} is not authorised")

    side_rec = prov.get("sidecar")
    if not isinstance(side_rec, dict):
        raise DeclaredEnvironmentError("provenance.sidecar is not an object")
    if Path(str(side_rec.get("path", ""))).resolve() != sp.resolve():
        raise DeclaredEnvironmentError(
            f"provenance names a different sidecar: {side_rec.get('path')!r}")
    if _sha256_file(sp) != side_rec.get("sha256"):
        raise DeclaredEnvironmentError("sidecar digest mismatch")

    _check_bound_any(prov, "source_old_sidecar", _OLD_SIDECAR_SCHEMAS,
                     "source old sidecar")
    _check_bound_any(prov, "policy_evidence", _POLICY_EVIDENCE_SCHEMAS,
                     "policy evidence")

    body = _strict_json(sp.read_text(encoding="utf-8"), "sidecar")
    if body.get("schema") != _SIDECAR_SCHEMA:
        raise DeclaredEnvironmentError(
            f"sidecar schema {body.get('schema')!r} != {_SIDECAR_SCHEMA!r}")
    if body.get("ratified_by") != "Rob":
        raise DeclaredEnvironmentError("sidecar ratifying authority is not Rob")
    decls = body.get("declarations")
    if not isinstance(decls, dict) or not decls:
        raise DeclaredEnvironmentError("sidecar.declarations must be a non-empty object")

    out: dict[str, str] = {}
    for task, rec in decls.items():
        if not isinstance(task, str) or not task:
            raise DeclaredEnvironmentError(f"invalid task id in sidecar: {task!r}")
        if not isinstance(rec, dict):
            raise DeclaredEnvironmentError(f"{task}: record is not an object")
        status = rec.get("status")
        if status == _SIDECAR_BLOCKED_STATUS:
            continue                      # explicitly blocked: no usable declaration
        if status in _SIDECAR_REJECTED_STATUSES:
            raise DeclaredEnvironmentError(
                f"{task}: status {status!r} is not ratified and cannot be used")
        if status != _SIDECAR_RATIFIED_STATUS:
            raise DeclaredEnvironmentError(f"{task}: unknown sidecar status {status!r}")
        if rec.get("ratified_by") != "Rob":
            raise DeclaredEnvironmentError(f"{task}: record is not ratified by the authorised owner")
        minor = rec.get("declared_python_minor")
        if not isinstance(minor, str) or declared_minor_tuple(minor) is None:
            raise DeclaredEnvironmentError(f"{task}: malformed declared_python_minor {minor!r}")
        if rec.get("requires_python_compatibility") != "OK":
            raise DeclaredEnvironmentError(
                f"{task}: compatibility {rec.get('requires_python_compatibility')!r} is not OK")
        out[task] = minor
    return out


def _declare_from_input(task_id: str, inputs: Path) -> str | None:
    """The task's OWN declaration from its input record, or None."""
    try:
        rec = json.loads((Path(inputs) / f"{task_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    install = rec.get("install_config") or {}
    for value in (rec.get("declared_python_minor"), install.get("base_image_name"),
                  rec.get("base_image_name")):
        if value:
            return str(value)
    return None


def _declared_minor_for(
    task_id: str,
    inputs: Path,
    *,
    sidecar: Path | str | None = None,
    provenance: Path | str | None = None,
) -> str | None:
    """The task's declared Python minor: input record first, else the RATIFIED sidecar.

    Conflicts, unratified records, and an untrustworthy sidecar when it is the
    only source all raise -- nothing is silently defaulted.
    """
    if not isinstance(task_id, str) or not task_id.strip():
        raise DeclaredInterpreterError(f"invalid task id: {task_id!r}")
    from_input = _declare_from_input(task_id, inputs)
    try:
        side = load_declared_environments(sidecar, provenance)
    except DeclaredEnvironmentError:
        if from_input:
            return from_input          # the input record is its own authority
        raise
    from_side = side.get(task_id)
    if from_input and from_side:
        a = declared_minor_tuple(from_input)
        b = declared_minor_tuple(from_side)
        if a != b:
            raise DeclaredEnvironmentError(
                f"{task_id}: input declares {from_input!r} but sidecar declares {from_side!r}")
        return from_input
    return from_input or from_side


def available_interpreters(
    prefer: tuple[tuple[int, int], ...] | None = None,
) -> list[str]:
    """Verified installed interpreters, discovered once per process.

    ``prefer`` narrows the candidate set to an explicit minor list.  When it is
    supplied, an empty discovery result is returned AS EMPTY: the static
    ``INTERPRETERS`` fallback contains the preference default and must never be
    substituted for a declared minor.
    """
    key = tuple(prefer) if prefer is not None else None
    if key not in _DISCOVERED_INTERPRETERS:
        try:
            if prefer is None:
                found = discover_interpreters(version_of=_version_of)
                _DISCOVERED_INTERPRETERS[key] = found or list(INTERPRETERS)
            else:
                _DISCOVERED_INTERPRETERS[key] = discover_interpreters(
                    version_of=_version_of, prefer=prefer
                )
        except Exception:  # noqa: BLE001 - never let discovery break a build
            _DISCOVERED_INTERPRETERS[key] = (
                list(INTERPRETERS) if prefer is None else []
            )
    return _DISCOVERED_INTERPRETERS[key]


def pick_interpreter(
    project: Path,
    *,
    prefer: tuple[tuple[int, int], ...] | None = None,
) -> str:
    """Choose the first available interpreter the project's metadata accepts.

    ``prefer`` restricts the search to the given minors.  With ``prefer=None``
    the documented preference order applies (legacy behaviour, retained only for
    callers that have no declared environment).
    """
    bounds = _requires_python(project)
    candidates = available_interpreters(prefer)
    for candidate in candidates:
        if not Path(candidate).is_file():
            continue
        if bounds is None:
            return candidate
        lo, hi = bounds
        ver = _version_of(candidate)
        if lo <= ver < hi:
            return str(candidate)
    # Two distinct failures, reported differently: nothing was discovered for the
    # requested minor(s), or something was discovered and the project's own bound
    # rejected it.  Collapsing them into one message made an ABSENT runtime look
    # like a rejected one, which is the wrong repair (install vs relax the bound).
    wanted = prefer if prefer is not None else PREFERRED_PYTHON_MINORS
    if not candidates:
        raise DeclaredInterpreterError(
            "no available interpreter for declared minor(s) "
            + ", ".join(f"{major}.{minor}" for major, minor in wanted)
            + f": searched {list(INTERPRETER_SEARCH_ROOTS)}"
            + "; install that runtime or add its root to INTERPRETER_SEARCH_ROOTS"
        )
    raise DeclaredInterpreterError(
        f"no available interpreter satisfies requires-python={bounds!r}: "
        f"discovered {candidates}"
    )


def resolve_declared_interpreter(project: Path, declared_minor: str | int | None) -> str:
    """Interpreter for a task's DECLARED minor, or fail closed."""
    minor = declared_minor_tuple(declared_minor)
    if minor is None:
        raise DeclaredInterpreterError(
            f"declared Python minor is missing or unparseable: {declared_minor!r}; "
            "refusing to fall back to the preference default"
        )
    bounds = _requires_python(project)
    if bounds is not None and not (bounds[0] <= minor < bounds[1]):
        raise DeclaredInterpreterError(
            f"declared Python minor {minor[0]}.{minor[1]} violates the project's "
            f"requires-python={bounds!r}"
        )
    return pick_interpreter(project, prefer=(minor,))


def _rmtree(path: Path) -> None:
    """Remove a tree that may contain read-only git objects.

    ``--filter=blob:none`` clones leave pack files read-only, and
    ``shutil.rmtree`` then raises ``PermissionError`` on Windows, which left a
    half-built workspace behind and made the next clone fail on a
    non-empty destination. Clear the read-only bit before removal.
    """
    if not path.exists():
        return
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            try:
                os.chmod(os.path.join(root, name), stat.S_IWRITE)
            except OSError:
                pass
    shutil.rmtree(path, ignore_errors=True)


#: Historical location of preserved workspaces.  The current implementation
#: derives it from the workspace root inside :func:`preserve_previous_workspace`,
#: so a different root (a test, another host) keeps its evidence beside its own
#: workspaces.
PRESERVED_WORKSPACES = ROOT / "_preserved_workspaces"


def preserve_previous_workspace(
    ws: Path, *, root: Path | None = None, reason: str = "retry",
) -> str | None:
    """Move an existing workspace ASIDE; never delete the previous attempt.

    The builder used to ``rmtree`` ``repo``, ``repo_gold`` and the venvs at the
    start of every build.  A retry therefore destroyed the only copy of the
    artifacts that explain why the previous attempt failed -- the workspace, its
    install plan and any partial logs -- so "three attempts" could silently
    mean "three attempts' evidence for one".  Preserving is also the
    non-destructive direction: a failed move raises instead of falling back to
    a delete.

    Only a workspace strictly inside ``root`` (default :data:`ROOT`) may be
    moved; anything else is refused, so a mistyped path can never relocate a
    tree outside the pilot root.
    """
    workspace = Path(ws)
    if not workspace.exists():
        return None
    root_path = Path(root if root is not None else ROOT).resolve()
    try:
        workspace.resolve().relative_to(root_path)
    except ValueError as exc:
        raise RuntimeError(
            f"WORKSPACE_PRESERVATION_REFUSED: {workspace} is not inside {root_path}"
        ) from exc
    preserved_root = root_path / "_preserved_workspaces"
    preserved_root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    destination = preserved_root / f"{workspace.name}.{reason}.{stamp}"
    suffix = 1
    while destination.exists():
        destination = preserved_root / f"{workspace.name}.{reason}.{stamp}.{suffix}"
        suffix += 1
    shutil.move(str(workspace), str(destination))
    return str(destination)


_PERMIT_UNAUTHORIZED_STUB_BUILD: bool = False

#: Repository root, used only to locate the independently-read registration
#: artifacts.  Never used as an authorization source.
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REGISTRATION_FILE = REPO_ROOT / "data/f2_population/registration/registered_tasks.json"
DEFAULT_REGISTRATION_MANIFEST = (
    REPO_ROOT / "qwen_train/curriculum/f2_registration/registration_manifest.json"
)


def _stub_build_permitted() -> bool:
    """The unauthorized-stub escape hatch is test-only.

    A production process cannot enable it through an environment variable or
    configuration; even the module global only takes effect inside a running
    pytest test, so an in-process flip outside a test still fails closed.
    """
    return _PERMIT_UNAUTHORIZED_STUB_BUILD and bool(os.environ.get("PYTEST_CURRENT_TEST"))


def _independent_production_binding(
    *,
    task_id: str | None,
    manifest_file: Path | str | None,
    registration_file: Path | str | None,
    registration_manifest_file: Path | str | None,
) -> dict[str, str]:
    """Recompute every production binding from the artifacts the build consumes.

    The signed record is never the source of the expected value: the build
    manifest and registration artifacts are read here and hashed.  The task is
    scoped to the signed manifest's task set.
    """
    mf = Path(
        manifest_file
        or os.environ.get("F2_BUILD_MANIFEST")
        or (ROOT / "build_manifest.json")
    )
    rf = Path(
        registration_file
        or os.environ.get("F2_REGISTRATION_FILE")
        or DEFAULT_REGISTRATION_FILE
    )
    rmf = Path(
        registration_manifest_file
        or os.environ.get("F2_REGISTRATION_MANIFEST_FILE")
        or DEFAULT_REGISTRATION_MANIFEST
    )
    for label, path in (
        ("build manifest", mf),
        ("registration file", rf),
        ("registration manifest", rmf),
    ):
        if not path.is_file():
            raise RuntimeError(f"BUILD_NOT_AUTHORIZED: {label} missing: {path}")
    try:
        manifest_doc = json.loads(mf.read_text("utf-8"))
    except Exception as exc:  # noqa: BLE001 - unreadable manifest is fail-closed
        raise RuntimeError(
            f"BUILD_NOT_AUTHORIZED: unparseable build manifest: {exc}"
        ) from exc
    if not isinstance(manifest_doc, Mapping):
        raise RuntimeError("BUILD_NOT_AUTHORIZED: build manifest is not an object")
    if task_id and task_id not in manifest_doc:
        raise RuntimeError(
            f"BUILD_NOT_AUTHORIZED: task {task_id!r} not in the signed build manifest"
        )
    try:
        provenance = json.loads(rmf.read_text("utf-8"))
    except Exception as exc:  # noqa: BLE001 - unreadable provenance is fail-closed
        raise RuntimeError(
            f"BUILD_NOT_AUTHORIZED: unparseable registration manifest: {exc}"
        ) from exc
    content_digest = str(
        provenance.get("registration_content_sha256")
        if isinstance(provenance, Mapping)
        else ""
    )
    if len(content_digest) != 64 or any(
        c not in "0123456789abcdef" for c in content_digest
    ):
        raise RuntimeError(
            "BUILD_NOT_AUTHORIZED: registration manifest has no 64-hex "
            "registration_content_sha256"
        )
    return {
        "candidate_manifest_digest": _sha256_file(mf),
        "registration_sha256": content_digest,
        "registration_manifest_sha256": _sha256_file(rf),
    }


def _verify_build_authorization(
    grant_path: Path | str | None = None,
    *,
    task_id: str | None = None,
    authorized_run: Any | None = None,
    manifest_file: Path | str | None = None,
    registration_file: Path | str | None = None,
    registration_manifest_file: Path | str | None = None,
) -> Mapping[str, Any]:
    """Verify an authorization grant for building a task, or fail closed.

    Enforces that untrusted build backend hooks (pip install, uv sync) cannot
    run without a cryptographically valid authorization grant for stage S8-001
    and that the target task is inside the authorized selection.
    """
    if authorized_run is not None:
        from qwen_train.f2_s8_v2_gate import (
            S8ProbeAuthorizedRun,
            check_probe_grant_still_valid,
        )

        if isinstance(authorized_run, S8ProbeAuthorizedRun):
            if task_id and task_id not in authorized_run.selected_task_ids:
                raise RuntimeError(
                    f"BUILD_NOT_AUTHORIZED: task {task_id!r} not in authorized probe selection"
                )
            check_probe_grant_still_valid(authorized_run)
            return dict(authorized_run.record)
        if hasattr(authorized_run, "task_ids") and task_id:
            if task_id not in authorized_run.task_ids:
                raise RuntimeError(
                    f"BUILD_NOT_AUTHORIZED: task {task_id!r} not in authorized task_ids"
                )
        if hasattr(authorized_run, "record"):
            return dict(authorized_run.record)
        return {"id": getattr(authorized_run, "grant_id", "")}

    grant = grant_path or os.environ.get("F2_AUTHORIZATION_FILE")
    if not grant or not Path(grant).is_file():
        raise RuntimeError("BUILD_NOT_AUTHORIZED: missing or unreadable authorization grant file")

    gp = Path(grant)
    try:
        doc = json.loads(gp.read_text("utf-8"))
        rec = doc.get("record") if isinstance(doc, dict) and "record" in doc else doc
    except Exception as exc:
        raise RuntimeError(f"BUILD_NOT_AUTHORIZED: unparseable grant file: {exc}") from exc

    ops = rec.get("operations") or []
    is_probe = "build_probe" in ops or (
        rec.get("schema") == "f2-authorization/v2" and rec.get("target_credited") == 0
    )

    if is_probe:
        from qwen_train.f2_s8_v2_gate import authorize_s8_probe_run

        reg_file = (
            os.environ.get("F2_REGISTRATION_FILE")
            or "data/f2_population/registration/registered_tasks.json"
        )
        compat_file = (
            os.environ.get("F2_COMPAT_REPORT_FILE")
            or "data/f2_population/plugin_compat/plugin_compat_report.json"
        )
        auth_run = authorize_s8_probe_run(
            grant_path=gp,
            registration_file=reg_file,
            compat_report_file=compat_file,
            # Verification is not execution: it must not reserve probe budget.
            reserve_execution=False,
        )
        if task_id and task_id not in auth_run.selected_task_ids:
            raise RuntimeError(
                f"BUILD_NOT_AUTHORIZED: task {task_id!r} not in authorized probe selection"
            )
        return dict(auth_run.record)
    else:
        from qwen_train.f2_authorization import verify_authorization

        if "base_gold_production" not in ops:
            raise RuntimeError(
                f"BUILD_NOT_AUTHORIZED: grant does not authorise S8-001 build operations (ops={ops!r})"
            )
        binding = _independent_production_binding(
            task_id=task_id,
            manifest_file=manifest_file,
            registration_file=registration_file,
            registration_manifest_file=registration_manifest_file,
        )
        verified = verify_authorization(
            gp,
            stage="S8-001",
            operation="base_gold_production",
            binding=binding,
        )
        return verified


def build(task_id: str, url: str, *, inputs: Path = INPUTS,
          patches: Path = PATCHES,
          declared_python_minor: str | int | None = None,
          grant_file: Path | str | None = None,
          authorized_run: Any | None = None,
          manifest_file: Path | str | None = None,
          registration_file: Path | str | None = None,
          registration_manifest_file: Path | str | None = None,
          root: Path | str | None = None) -> dict:
    rec = json.loads((Path(inputs) / f"{task_id}.json").read_text(encoding="utf-8"))
    base_commit = rec["base_commit"]
    ws = workspace_path(root, task_id)
    repo = ws / "repo"
    gold_repo = ws / "repo_gold"

    # Fail closed BEFORE the workspace is touched or anything is cloned: the
    # pytest policy is an owner decision (D1) and no build may proceed under an
    # unratified one.  Doing this first also means a blocked build cannot rotate
    # a previous attempt's preserved evidence.
    try:
        require_ratified_pytest_admissibility()
    except PytestPolicyError as exc:
        raise RuntimeError(str(exc)) from exc

    # The interpreter minor is an INPUT, never a default: an unparseable or
    # absent declaration must fail closed instead of silently becoming (3, 10).
    # Checked here, with the policy gate, so neither deterministic refusal can
    # rotate a previous attempt's preserved evidence.
    py_minor = declared_minor_tuple(declared_python_minor)
    if py_minor is None:
        raise DeclaredInterpreterError(
            f"declared Python minor is missing or unparseable: "
            f"{declared_python_minor!r}; refusing to assume a policy value")

    # Fail closed BEFORE the workspace is touched or anything is cloned:
    # a direct call to build() must not be able to prepare or execute a workspace
    # without an authorized S8 grant.
    if not _stub_build_permitted():
        _verify_build_authorization(
            grant_file,
            task_id=task_id,
            authorized_run=authorized_run,
            manifest_file=manifest_file,
            registration_file=registration_file,
            registration_manifest_file=registration_manifest_file,
        )

    # Preserve, do not delete: a retry must not overwrite the evidence that
    # explains the previous attempt (R1-D).
    preserved = preserve_previous_workspace(ws, root=root)
    ws.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    run([GIT, "clone", "--filter=blob:none", "--quiet", url, str(repo)])
    run([GIT, "checkout", "--quiet", base_commit], cwd=repo)
    run([GIT, "apply", str(Path(patches) / f"{task_id}.test_patch")], cwd=repo)

    # Python layout differs per repository. A monorepo's installable project is
    # often NOT the repo root (awslabs/mcp keeps 58 subprojects under src/,
    # cirq keeps the real package in cirq-core), so the root is RESOLVED from
    # declared metadata rather than assumed. `discover_project_root` existed but
    # was never called here, and the only special case was hardcoded.
    project_root = resolve_task_project_root(
        repo,
        test_cmd=declared_test_cmd(rec),
        patch_text=str(rec.get("patch") or ""),
        fail_to_pass=rec.get("FAIL_TO_PASS") or (),
        override="hypothesis-python" if task_id.startswith("HypothesisWorks") else "",
    ).resolve()
    sub = project_root.relative_to(repo.resolve()).as_posix()
    sub = "" if sub == "." else sub
    project = repo / sub if sub else repo
    gold_project = gold_repo / sub if sub else gold_repo

    # Gold tree is cut BEFORE either venv exists, so the copy cannot inherit a
    # base virtualenv (which would waste gigabytes and, worse, shadow the gold
    # environment that is about to be built for it).
    shutil.copytree(repo, gold_repo, symlinks=True,
                    ignore=shutil.ignore_patterns(".venv", ".venvuv", "__pycache__"))
    run([GIT, "apply", str(Path(patches) / f"{task_id}.gold_patch")], cwd=gold_repo)

    # Fail closed at the UNTRUSTED boundary itself, not only in main(): a direct
    # call to build() must not be able to pip-install a task (and so run its
    # build backend) with an administrator identity.
    require_unprivileged_identity("qwen_train.f2_s8_pilot_build.build")
    base_python = resolve_declared_interpreter(project, declared_python_minor)
    extra = EXTRA_DEPS.get(task_id, [])
    # The declared (never invented) per-task build adjustments.  ``groups`` /
    # ``extras`` name the project's OWN dependency groups and extras; they were
    # recorded in ``build_identity`` before but never acted on by the installer,
    # so an adjustment naming ``test`` installed nothing beyond the pytest
    # plugins that happen to live there.
    adjustment: dict[str, Any] = dict(DECLARED_ADJUSTMENTS.get(task_id, {}))
    ctx = project_install_context(project)
    # Deterministic pytest version selection according to Rule A / B / C.
    # Every declaration source (project deps, extras, groups, Poetry/Hatch,
    # setup.cfg, setup.py, tox.ini, requirements files and the lockfile) is
    # intersected, and the OWNER-RATIFIED admissibility gate is enforced here.
    pytest_sel = select_pytest_version(
        project=project,
        declared_python_minor=py_minor,
        dependency_inputs=ctx["dependency_inputs"],
        adjustment=adjustment,
        runtime_dependencies=ctx["runtime_dependencies"],
        require_owner_ratification=True,
    )
    if pytest_sel.get("status") == "ADMISSIBILITY_NOT_RATIFIED":
        raise RuntimeError(str(pytest_sel.get("detail")))
    if pytest_sel.get("status") == "CONFLICT":
        raise RuntimeError(f"PROJECT_PIN_CONFLICT: {pytest_sel.get('detail')}")
    pin = approved_pytest_requirement(pytest_sel)
    # UNION the plugins the project's OWN pytest configuration demands.  The
    # hard-coded table above is keyed by instance id and goes stale the moment a
    # DIFFERENT instance of the same project is built (the phase-2 set contains
    # ``python__mypy-19718``, not the ``-19767`` the table names, so xdist was
    # never installed and the declared ``-nauto`` aborted pytest with exit 4 --
    # recorded as an infrastructure failure with no JUnit).  Derived from the
    # checked-out project, so it cannot drift.
    derived = [p for p in pytest_config_plugins(project) if p not in extra]
    # ...and the plugins the DECLARED COMMAND requires, which is the other place
    # a project states a pytest option. ``addopts`` was the only source read, so
    # a task whose command carries ``-n 4`` and whose dependency groups omit
    # xdist aborted with exit 4 and produced no JUnit at all.
    for dist in command_plugins(declared_test_cmd(rec)):
        if dist not in extra and dist not in derived:
            derived.append(dist)
    if derived:
        extra = [*extra, *derived]
    # uv-owned tasks are identified by their DECLARED command; the pilot's
    # explicit table stays as an override so its behaviour cannot drift.
    uses_uv = task_id in UV_ALL_EXTRAS or uses_uv_environment(declared_test_cmd(rec))

    # Base and gold each get their OWN environment, editable-installed onto
    # their OWN tree. Sharing one venv would make the gold run import BASE
    # source through the editable .pth, so the gold patch could never take
    # effect and a correct fix would be scored as a failure. This is why
    # ``SWARM_S8_BASE_STATE_DIGEST`` and ``SWARM_S8_GOLD_STATE_DIGEST`` differ.
    #
    # Tasks whose DECLARED command is ``uv run pytest`` are excluded: uv owns
    # their environments (``repo/.venv`` and ``repo_gold/.venv``) and creates
    # them itself. Building a second, unused pip venv per tree only doubled the
    # delivered payload to no effect.
    envs: dict[str, str | None] = {"base": None, "gold": None}
    install_plan: dict[str, Any] = {}
    if not uses_uv:
        for label, proj in (("base", project), ("gold", gold_project)):
            venv = ws / (".venv" if label == "base" else ".venv_gold")
            run([base_python, "-m", "venv", str(venv)])
            vpy = venv / "Scripts" / "python.exe"
            run([str(vpy), "-m", "pip", "install", "--quiet",
                 "--disable-pip-version-check", "--upgrade", "pip"], timeout=1800)
            run([str(vpy), "-m", "pip", "install", "--quiet",
                 "--disable-pip-version-check", pin, *extra], timeout=1800)
            # The project's OWN declared pytest plugins (e.g. pytest-django).
            # Without them pytest ignores DJANGO_SETTINGS_MODULE and every
            # module that imports the framework fails COLLECTION, so the declared
            # FAIL_TO_PASS node is absent from the JUnit and can never pass.
            # Statically derived from the project's metadata -- never invented --
            # and taken from the SAME function the offline classifier reads, so
            # "what the builder installs" cannot drift from "what the classifier
            # checks".  Full declarations (specifier and marker included) are
            # passed through, so the project's own constraint is honoured.
            # Extras are NOT in scope: ``pip install -e .`` installs
            # ``[project].dependencies`` (recorded as runtime deps) but no
            # ``[project.optional-dependencies]`` extra -- "uv does not sync
            # extras by default" is the same rule for the uv path.
            context = project_install_context(proj)
            # The project's OWN declared test dependencies (PEP 621 test extra /
            # PEP 735 test group / a test-named requirements file).  ``pip
            # install -e .`` installs ``[project].dependencies`` and NO extra, so
            # a project that declares a test-only import under
            # ``[project.optional-dependencies]`` -- QuantEcon declares ``pandas``
            # only in its ``testing`` extra -- previously built an environment
            # whose collection aborted with ``ModuleNotFoundError`` and was
            # classified as an environment collection error.  Selection is
            # name-gated in :func:`declared_test_requirements`; a named source
            # that does not exist is BLOCKING here rather than a silent no-op.
            declared = declared_test_requirements(
                context["dependency_inputs"],
                groups=[str(g) for g in (adjustment.get("groups") or ())],
                extras=[str(e) for e in (adjustment.get("extras") or ())],
                project=proj,
            )
            blocking = [p for p in declared["problems"] if p.get("scope") == "blocking"]
            if blocking:
                detail = "; ".join(
                    f"{p.get('code')}:{p.get('detail')}" for p in blocking)
                raise RuntimeError(
                    f"declared test dependencies cannot be resolved: {detail}")
            plan = builder_install_plan(
                context["dependency_inputs"],
                pinned=[pin],
                extra_deps=extra,
                runtime_dependencies=context["runtime_dependencies"],
                test_requirements=declared["requirements"],
            )
            plan["declared_test_sources"] = declared["sources"]
            plan["declared_test_problems"] = declared["problems"]
            install_plan[label] = plan
            if plan["plugin_requirements"]:
                # ``pin`` is repeated on EVERY install command.  A plugin's own
                # metadata can require a newer pytest (the measured
                # ``awslabs__mcp-1154`` case: 8.3.5 requested, 9.1.1 observed
                # afterwards), so the approved constraint has to be part of each
                # resolution -- not only the first one.  pip then either keeps
                # the approved release or fails on an impossible intersection.
                run([str(vpy), "-m", "pip", "install", "--quiet",
                     "--disable-pip-version-check", pin,
                     *plan["plugin_requirements"]],
                    timeout=3600)
            # Declared test dependencies go in beside the plugins, BEFORE the
            # editable install, which is the pairing the PyPA dependency-groups
            # specification requires (a ``test`` group only works when the
            # toolchain also installs ``.``).
            if plan["test_requirements"]:
                # Constraints the project declares (-c) are handed to pip so its
                # own bounds apply; they are never installed as requirements.
                constraint_args: list[str] = []
                for constraint_file in declared.get("constraints") or ():
                    constraint_args.extend(["-c", str(constraint_file)])
                run([str(vpy), "-m", "pip", "install", "--quiet",
                     "--disable-pip-version-check", *constraint_args, pin,
                     *plan["test_requirements"]],
                    timeout=3600)
            run([str(vpy), "-m", "pip", "install", "--quiet",
                 "--disable-pip-version-check", pin, "-e", str(proj)],
                timeout=3600)
            # The plan above records what was ASKED for; this records what pip
            # actually RESOLVED (name + version of every installed distribution).
            # Without it a manifest digest cannot be checked against a later
            # environment, so "offline recreation verified" would be a claim
            # rather than a comparison.  A failed listing is recorded as a
            # problem rather than an empty dict, because an empty dict is
            # indistinguishable from an empty environment.
            try:
                listed = run(
                    [str(vpy), "-m", "pip", "list", "--format=json",
                     "--disable-pip-version-check"], timeout=300).stdout
                plan["resolved_distributions"] = {
                    str(d.get("name")): str(d.get("version"))
                    for d in (json.loads(listed) or [])
                }
            except Exception as exc:  # noqa: BLE001 - observational, must not mask a real build error
                plan["resolved_distributions"] = {}
                plan["resolved_distributions_error"] = str(exc)[:500]
            # Post-install drift protection (R1-A items 9-11): the version that
            # is INSTALLED must be the approved one.  This runs after the last
            # install of the side, so a plugin or the project upgrading pytest
            # afterwards is caught here instead of being discovered as an
            # unexplained outcome difference.
            plan["pytest_enforcement"] = {
                "approved_requirement": pin,
                "applied_to": ["pytest_baseline", "plugins",
                               "declared_test_requirements", "editable_project"],
                **assert_installed_pytest(vpy, pytest_sel["version"],
                                          label=f"{task_id}:{label}"),
            }
            envs[label] = str(vpy)

    # Syncing here -- on the acquisition host, with the network -- populates the
    # project venvs so the guest can run the same command with UV_OFFLINE=1 and
    # never reach for the network. uv's wheel cache is NOT required for that:
    # a complete venv plus an intact lockfile resolves with an empty cache.
    uv_exe: Path | None = None
    uv_evidence: dict[str, Any] | None = None
    if uses_uv:
        uv_exe = resolve_and_verify_uv()
        uv_evidence = {
            "path": str(uv_exe),
            "sha256": _sha256_file(uv_exe),
            "version": _uv_version(uv_exe),
        }
        # uv's `required-version` is a HARD gate. Checking it explicitly turns an
        # opaque "Failed to parse uv.lock / missing field version" into a named
        # toolchain incompatibility, which is a different class of failure from a
        # task defect and must not be recorded as one.
        required = declared_required_version(project)
        if required:
            installed = uv_evidence["version"]
            if installed and not _satisfies_version(installed, required):
                raise RuntimeError(
                    f"uv required-version: the project requires {required!r} but "
                    f"the installed uv is {installed!r}; UV_TOOLCHAIN_INCOMPATIBLE")
        # Sync in the RESOLVED project root, not blindly at the repo root: a
        # monorepo's lockfile belongs to the subproject.
        uv_enforcement: dict[str, Any] = {}
        for label, tree in (("base", project), ("gold", gold_project)):
            # Pin uv to the SAME interpreter this builder selected. Without
            # --python, uv discovers its own (newest) interpreter and chose
            # CPython 3.15 for a >=3.13 task, where psycopg-binary has no wheel.
            run([str(uv_exe), "sync", "--frozen", "--all-extras",
                 "--python", base_python], cwd=tree, timeout=3600)
            # uv owns this environment, so the approved pytest is whatever the
            # governing lockfile resolved -- and that is exactly what has to be
            # verified here rather than assumed from the lock.
            env_python = (tree / ".venv" / ("Scripts" if os.name == "nt" else "bin")
                          / ("python.exe" if os.name == "nt" else "python"))
            if not env_python.is_file():
                raise RuntimeError(
                    f"PYTEST_INSTALLED_UNVERIFIABLE: uv sync did not create "
                    f"{env_python}; the approved pytest {pytest_sel['version']} "
                    "cannot be verified")
            uv_enforcement[label] = assert_installed_pytest(
                env_python, pytest_sel["version"], label=f"{task_id}:{label}:uv")
        uv_evidence["pytest_enforcement"] = uv_enforcement

    return {
        "task_id": task_id, "base_commit": base_commit, "workspace": str(ws),
        "repo": str(repo), "gold_repo": str(gold_repo),
        "uses_uv": uses_uv,
        "venv": str(ws / ".venv"), "gold_venv": str(ws / ".venv_gold"),
        "python": envs["base"], "gold_python": envs["gold"],
        "project": str(project), "gold_project": str(gold_project),
        # The resolved install/test root relative to the repo. Recorded so the
        # pre-check and the guest run in the SAME tree the build installed.
        "project_subdir": sub,
        "base_interpreter": base_python if not uses_uv else "",
        "extra_deps": extra,
        # Provenance: exactly which packages this build installed on each side,
        # derived from the same source of truth the offline classifier checks.
        "install_plan": install_plan,
        # Which BUILDER produced this environment. Recorded so a workspace built
        # under an older contract cannot be pre-checked as if the current one had
        # produced it -- previously the builder version lived only in the
        # acquisition identity, which this manifest never carried and the
        # pre-check never recomputes, so a stale workspace passed silently.
        "builder": BUILDER_SOURCE,
        "pytest_selection": pytest_sel,
        "preserved_previous_workspace": preserved,
        "uv_evidence": uv_evidence,
        "requires_python": list(_requires_python(project)) if _requires_python(project) else None,
        "elapsed_s": int(time.time() - t0),
    }


def _uv_version(uv_path: Path | str | None = None) -> str:
    """The installed uv's version string, or ``""`` when uv cannot be run."""
    try:
        resolved = resolve_and_verify_uv(uv_path)
        out = run([str(resolved), "--version"], timeout=120).stdout
    except Exception:  # noqa: BLE001 - absence is handled by the caller
        return ""
    match = re.search(r"uv\s+(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?)", out)
    return match.group(1) if match else ""


def _satisfies_version(installed: str, spec: str) -> bool:
    """True when ``installed`` satisfies uv's ``required-version`` specifier."""
    try:
        from packaging.specifiers import InvalidSpecifier, SpecifierSet
        from packaging.version import InvalidVersion, Version

        return Version(installed) in SpecifierSet(str(spec))
    except (InvalidSpecifier, InvalidVersion, ValueError):
        # An unparseable spec is NOT permission to proceed: fail closed by
        # reporting incompatibility, which the caller turns into a named reason.
        return False


def classify_build_failure(exc: BaseException) -> str:
    """Map a build exception to an explicit reason code (never to "infrastructure").

    The classification is a deterministic text-signature match over the message
    the builder itself produced -- :func:`run` embeds the subprocess's exit code,
    stdout tail and stderr tail, so a pip resolver complaint, a wheel build
    failure and a git clone failure each arrive here verbatim rather than as an
    opaque ``RuntimeError``.

    Design rule: **no default that flatters the yield.**  A failure whose message
    carries no recognised signature returns ``BUILD_FAILURE_UNCLASSIFIED``, so a
    genuine repository or dependency defect is never relabelled an infrastructure
    failure to keep the pass rate up (D1 cause-classification).  Ordering matters:
    the declared-test-source message is checked before the generic pip signatures
    because it IS the "declared dependency could not be resolved" case.
    """
    if isinstance(exc, subprocess.TimeoutExpired):
        return "BUILD_TIMEOUT"
    msg = str(exc)
    if "BUILD_NOT_AUTHORIZED" in msg:
        return "BUILD_NOT_AUTHORIZED"
    if "BUILDER_TOOL_UNAVAILABLE" in msg or "uv executable not found" in msg:
        return "BUILDER_TOOL_UNAVAILABLE"
    if "BUILDER_TOOL_CORRUPT" in msg or "SHA-256 mismatch" in msg:
        return "BUILDER_TOOL_CORRUPT"
    if "PROFILE_ISOLATION_VIOLATION" in msg:
        return "PROFILE_ISOLATION_VIOLATION"
    # R1-A: the pytest policy boundary.  These are DETERMINISTIC build-policy
    # reasons -- retrying changes nothing until the owner decision, the tool or
    # the project's declarations change.
    if "PYTEST_ADMISSIBILITY_NOT_RATIFIED" in msg:
        return "PYTEST_ADMISSIBILITY_NOT_RATIFIED"
    if "PYTEST_PIN_UNRESOLVED" in msg:
        return "PYTEST_PIN_UNRESOLVED"
    if "PYTEST_INSTALLED_DRIFT" in msg:
        return "PYTEST_INSTALLED_DRIFT"
    if "PYTEST_INSTALLED_UNVERIFIABLE" in msg:
        return "PYTEST_INSTALLED_UNVERIFIABLE"
    if "WORKSPACE_PRESERVATION_REFUSED" in msg:
        return "WORKSPACE_PRESERVATION_REFUSED"
    if "PROJECT_PIN_CONFLICT" in msg or "conflicting declarations for pytest" in msg:
        return "PROJECT_PIN_CONFLICT"
    if "PYTHON_FLOOR_INCOMPATIBLE" in msg:
        return "PYTHON_FLOOR_INCOMPATIBLE"
    if "declared test dependencies cannot be resolved:" in msg:
        return "DECLARED_TEST_SOURCE_UNRESOLVABLE"
    if "UV_TOOLCHAIN_INCOMPATIBLE" in msg or "uv required-version:" in msg:
        return "UV_TOOLCHAIN_INCOMPATIBLE"
    # A newer uv.lock schema that this uv cannot parse (measured: revision 2 may
    # omit `version` on the editable root package, which uv 0.5.9's typed parser
    # rejects).  This is a TOOLCHAIN limit, not a task defect.
    if "Failed to parse `uv.lock`" in msg or "missing field `version`" in msg:
        return "UV_TOOLCHAIN_INCOMPATIBLE"
    # A POSIX-only dependency cannot be built on Windows at any interpreter
    # version. Measured: `uwsgi`'s sdist calls `os.uname()`, which does not exist
    # on Windows, so `sissbruecker__linkding-1175` failed AFTER the interpreter
    # was corrected -- the interpreter was never the real blocker.
    if ("os.uname" in msg or "has no attribute 'uname'" in msg
            or "No module named 'fcntl'" in msg):
        return "PLATFORM_INCOMPATIBLE"
    if "no interpreter satisfies requires-python" in msg:
        return "INTERPRETER_UNAVAILABLE"
    pip_resolution = (
        "Could not find a version that satisfies the requirement",
        "No matching distribution found",
        "ResolutionImpossible",
        "pip's dependency resolver did not find a match",
        "conflicting dependencies",
        "has requirement ",
    )
    if any(sig in msg for sig in pip_resolution):
        return "DEPENDENCY_RESOLUTION_FAILURE"
    pip_install = (
        "Failed building wheel",
        "error: subprocess-exited-with-error",
        "Could not install packages",
        "ERROR: Could not install",
        "No space left on device",
        "ERROR: These packages are missing",
    )
    if any(sig in msg for sig in pip_install):
        return "DEPENDENCY_INSTALLATION_FAILURE"
    if re.search(r"git(?:\.exe)?\s+(?:clone|checkout|apply|fetch|submodule|worktree)",
                 msg, re.IGNORECASE):
        return "SOURCE_ACQUISITION_FAILURE"
    return "BUILD_FAILURE_UNCLASSIFIED"


_REAL_BUILD = build


def main(argv=None) -> int:
    # Fail closed BEFORE anything repository-controlled can run: this stage
    # pip-installs the task, which executes its build backend.
    require_unprivileged_identity("qwen_train.f2_s8_pilot_build")
    ap = argparse.ArgumentParser()
    ap.add_argument("task_ids", nargs="*")
    ap.add_argument("--manifest", default=str(ROOT / "build_manifest.json"))
    ap.add_argument("--inputs", default=str(INPUTS),
                    help="task record directory (one <task_id>.json per task)")
    ap.add_argument("--patches", default=str(PATCHES),
                    help="directory holding <task_id>.test_patch / .gold_patch")
    ap.add_argument("--grant-file", default=os.environ.get("F2_AUTHORIZATION_FILE"),
                    help="Path to signed S8 authorization grant file")
    ap.add_argument("--registration-file",
                    default=os.environ.get("F2_REGISTRATION_FILE")
                    or str(DEFAULT_REGISTRATION_FILE),
                    help="registration id list whose real bytes the grant binds")
    ap.add_argument("--registration-manifest",
                    default=os.environ.get("F2_REGISTRATION_MANIFEST_FILE")
                    or str(DEFAULT_REGISTRATION_MANIFEST),
                    help="registration provenance manifest (content digest source)")
    args = ap.parse_args(argv)

    grant_file = args.grant_file
    ids = args.task_ids or list(REPOS)

    # Live execution requires a verified authorization grant.
    # Unit tests that explicitly set _PERMIT_UNAUTHORIZED_STUB_BUILD without
    # executing builds are permitted to exercise manifest logic.
    if not _stub_build_permitted():
        if not grant_file or not Path(grant_file).is_file():
            print("BUILD_NOT_AUTHORIZED: missing or unreadable authorization grant file", flush=True)
            return 2

        try:
            for tid in ids:
                _verify_build_authorization(
                    grant_file,
                    task_id=tid,
                    manifest_file=args.manifest,
                    registration_file=args.registration_file,
                    registration_manifest_file=args.registration_manifest,
                )
        except Exception as exc:
            print(f"BUILD_NOT_AUTHORIZED: {exc}", flush=True)
            return 2
    manifest_path = Path(args.manifest)
    inputs = Path(args.inputs)
    patches = Path(args.patches)

    # The manifest this run writes describes EXACTLY the tasks this run was asked
    # to build. Merging into whatever manifest was already on disk let a subset
    # build carry every earlier ``status="built"`` entry forward -- and the runner
    # selects every ``status="built"`` entry, so a stale or unauthorized workspace
    # entered the runnable set that the single-use S8-001 grant is bound to
    # (docs/EXPERIMENT_J_F2_S8_EXECUTION_REVISION_AND_CHECKOUT_PLAN.md §3.4). A
    # manifest is the Stage-1 deliverable the grant binds; it must not accumulate
    # entries this authorization did not name.
    carried_forward: list[str] = []
    archived_manifest: str | None = None
    if manifest_path.is_file():
        # Preserve, never overwrite: the file is replaced at the end of the run,
        # so an unarchived copy would be the only record of the previous
        # attempt's result.  The archive name is chosen to be free; an existing
        # archive is never reused or deleted.
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        archive = manifest_path.with_name(
            f"{manifest_path.stem}.previous-{stamp}{manifest_path.suffix}")
        suffix = 1
        while archive.exists():
            archive = manifest_path.with_name(
                f"{manifest_path.stem}.previous-{stamp}.{suffix}{manifest_path.suffix}")
            suffix += 1
        shutil.copy2(manifest_path, archive)
        archived_manifest = str(archive)
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - unreadable is not a licence to merge
            previous = {}
        if isinstance(previous, dict):
            requested = set(ids)
            carried_forward = sorted(str(k) for k in previous if k not in requested)
    if archived_manifest:
        print(f"ARCHIVED previous manifest -> {archived_manifest}", flush=True)
    if carried_forward:
        print(
            "NOT CARRIED FORWARD (not part of this run, so not part of the manifest "
            "the grant binds): " + ", ".join(carried_forward),
            flush=True,
        )
    manifest: dict[str, Any] = {}

    for task_id in ids:
        url = resolve_repo_url(task_id, inputs)
        if not url:
            print(f"SKIP {task_id}: no repository mapping", flush=True)
            continue
        print(f"BUILD {task_id} ...", flush=True)
        try:
            entry = build(
                task_id, url, inputs=inputs, patches=patches,
                declared_python_minor=_declared_minor_for(task_id, inputs),
                grant_file=grant_file,
                manifest_file=manifest_path,
                registration_file=args.registration_file,
                registration_manifest_file=args.registration_manifest,
            )
        except Exception as exc:  # noqa: BLE001 - recorded, not fatal to the batch
            reason = classify_build_failure(exc)
            print(f"  FAILED [{reason}]: {type(exc).__name__}: {exc}", flush=True)
            manifest[task_id] = {
                "status": "build_failure",
                "reason_code": reason,
                "detail": str(exc)[:2000],
            }
        else:
            entry["status"] = "built"
            manifest[task_id] = entry
            print(f"  ok in {entry['elapsed_s']}s -> {entry['workspace']}", flush=True)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=1, sort_keys=True),
                                 encoding="utf-8")

    print(json.dumps({k: v.get("status") for k, v in manifest.items()}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
