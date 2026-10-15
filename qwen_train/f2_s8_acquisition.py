"""F2 S8 acquisition: metadata-driven task resolution and workspace identity.

Why this module exists
----------------------
``f2_s8_pilot_build`` resolved tasks from a hand-written map (5 repositories, 5
hand-authored patches, a hand-coded sub-layout, hand-discovered extra deps).
That cannot be scaled to the registered population and it silently depended on
who authored it.  This module derives everything from the pinned corpus and the
operative registration instead:

* the ordered, eligible task list comes from the **registration** (frozen);
* each task's metadata comes from the **acquired corpus** (pinned, hashed);
* the project root, interpreter and dependency inputs are discovered by
  **static metadata inspection only** (``pyproject.toml`` / ``setup.cfg`` /
  ``requirements*.txt``).

Code-execution boundary
-----------------------
Nothing here runs a task-declared test command and nothing here installs a
package.  That is deliberate: a PEP 517 build backend is *executed* to build a
project ("All hooks are run with working directory set to the root of the source
tree" -- PEP 517, https://peps.python.org/pep-0517/, accessed 2026-10-15), and a
declared test command is repository-controlled code.  Both belong to the
acquisition/build boundary under existing authority, never to "just find out
which module is missing".

Sources consulted (accessed 2026-10-15)
---------------------------------------
* PEP 517 (Final) -- build backends are imported and executed; build
  requirements must be importable, including for spawned subprocesses.
* PEP 660 (Final) -- an editable install binds a project to ONE source tree.
* uv, "Locking and syncing" (docs.astral.sh/uv/concepts/projects/sync) --
  ``uv run`` locks+syncs automatically; ``--locked``/``--frozen``/``--no-sync``;
  "uv does not sync extras by default"; dev deps live in ``[dependency-groups]``
  (PEP 735).
* uv environment reference -- ``UV_FIND_LINKS``/``UV_OFFLINE``.
"""
from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from qwen_train.f2_population_acquire import SOURCES, load_acquired
from qwen_train.f2_population_build import source_to_screen_row
from qwen_train.f2_registration import REGISTRATION_ID, eligible, is_pytest_command

__all__ = [
    "ACQUISITION_SCHEMA",
    "AcquisitionError",
    "TaskInputs",
    "BUILDER_SOURCE",
    "builder_install_plan",
    "declared_test_plugins",
    "declared_test_plugins_report",
    "dependency_inputs_from_pyproject",
    "dependency_metadata_status",
    "install_context_from_pyproject",
    "project_install_context",
    "runtime_dependencies_from_pyproject",
    "uv_default_groups_from_pyproject",
    "normalize_distribution_name",
    "parse_requirements_names",
    "resolve_dependency_groups",
    "load_corpus_rows",
    "scan_cross_source_conflicts",
    "load_registration_order",
    "resolve_task",
    "select_tasks",
    "repo_url_for",
    "discover_project_root",
    "requires_python",
    "select_interpreter",
    "static_dependency_inputs",
    "build_identity",
]

ACQUISITION_SCHEMA = "f2_s8_acquisition_v1"

#: The builder identity folded into every build identity.  Changing the builder
#: MUST change the identity, so a stale workspace can never be reused silently.
BUILDER_SOURCE = "qwen_train/f2_s8_acquisition.py + qwen_train/f2_s8_pilot_build.py"

#: Declarative, reproducible exceptions.  Each is a *build* decision, recorded in
#: the build identity; none changes a declared command, test list or patch.
#: ``pytest_pin`` mirrors the existing builder (a pinned conftest-compatible
#: pytest); ``extras``/``groups`` name the project's OWN declared dependency
#: groups (PEP 735 / optional-dependencies) -- never an invented package.
DECLARED_ADJUSTMENTS: dict[str, dict[str, Any]] = {
    "python__mypy-19767": {"pytest_pin": "pytest==8.3.5", "groups": ["test"]},
    "stanfordnlp__dspy-8739": {"pytest_pin": "pytest==8.3.5", "groups": ["test"]},
    "HypothesisWorks__hypothesis-4532": {"pytest_pin": "pytest==8.3.5"},
}

DEFAULT_PYTEST_PIN = "pytest==8.3.5"


class AcquisitionError(Exception):
    """The acquisition contract was violated."""


# ---------------------------------------------------------------------------
# Corpus + registration
# ---------------------------------------------------------------------------
def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    """SHA-256 of a file, streamed in chunks.

    The pinned corpora are multi-gigabyte JSONL; ``Path.read_bytes()`` on one of
    them materialises the whole file in memory.  Provenance must not require that.
    """
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def load_corpus_rows(
    acquired_paths: Mapping[str, Path],
    *,
    only: Iterable[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Load the pinned corpus into ``instance_id -> row``, with provenance.

    Both source schemas are read through the repository's own loader
    (:func:`f2_population_acquire.load_acquired`), which preserves every upstream
    column.  A duplicate instance id across sources is REFUSED rather than
    silently preferring one: two records that disagree about a task's commit or
    patches are an ambiguity, not a tie to break.

    ``only`` restricts the result to a set of instance ids.  The pinned corpus is
    ~3 GB of JSONL and a single row can carry a 500 KB ``PASS_TO_PASS`` list, so
    a batch must not materialise all of it; when ``only`` is given the file is
    streamed line by line and the rest is discarded.  The provenance digest is
    still taken over the WHOLE file, so identity is unchanged by this shortcut.
    """
    wanted = None if only is None else {str(x) for x in only}
    rows: dict[str, dict[str, Any]] = {}
    for source_key in acquired_paths:
        if source_key not in SOURCES:
            raise AcquisitionError(
                f"unknown source {source_key!r}; known: {sorted(SOURCES)}")
    for source_key, path in acquired_paths.items():
        p = Path(path)
        if not p.is_file():
            raise AcquisitionError(f"corpus file missing: {p}")
        digest = _sha256_file(p)
        if wanted is None:
            source_rows = load_acquired(p)
        else:
            source_rows = _stream_acquired(p, wanted)
        for row in source_rows:
            task_id = str(row.get("instance_id") or "")
            if not task_id:
                raise AcquisitionError(f"{p.name}: a record has no instance_id")
            prior = rows.get(task_id)
            if prior is not None:
                # The union already de-duplicated the sources, so a task present in
                # both files must describe the SAME task.  Compare the canonical
                # views; a field one source simply does not carry is not a
                # conflict, but a DIFFERENT commit or a DIFFERENT patch is.
                a = _canonical_row(str(prior["_source"]), prior)
                b = _canonical_row(source_key, row)
                # ``_source`` is provenance, not task metadata, so it is excluded.
                def _cmp(d):
                    return _canonical({k: v for k, v in d.items()
                                       if k not in ("_patch", "_source")})

                if _cmp(a) != _cmp(b):
                    raise AcquisitionError(
                        f"instance_id {task_id!r} appears in more than one source with "
                        f"DISAGREEING metadata ({prior['_source']} vs {source_key})")
                pa, pb = str(a.get("_patch") or ""), str(b.get("_patch") or "")
                if pa and pb and pa != pb:
                    raise AcquisitionError(
                        f"instance_id {task_id!r} carries DIFFERENT gold patches in "
                        f"{prior['_source']} and {source_key}")
                continue
            rows[task_id] = {**row, "_source": source_key, "_source_digest": digest}
    return rows


def _iter_acquired(path: Path):
    """Yield every validated row of a normalised JSONL (memory: one row at a time)."""
    import json as _json

    with Path(path).open(encoding="utf-8") as handle:
        for n, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = _json.loads(line)
            except Exception as exc:  # noqa: BLE001 - fail closed
                raise AcquisitionError(f"{Path(path).name}:{n} is not JSON: {exc}") from exc
            if not isinstance(row, dict):
                raise AcquisitionError(f"{Path(path).name}:{n} is not a JSON object")
            yield row


def _stream_acquired(path: Path, wanted: set[str]) -> list[dict[str, Any]]:
    """Stream a normalised JSONL, keeping only ``wanted`` ids.

    Same validation as the full loader: a malformed line is refused, never
    skipped, because a corpus that cannot be parsed is not a corpus.
    """
    return [row for row in _iter_acquired(path)
            if str(row.get("instance_id") or "") in wanted]


def load_registration_order(ordered_metadata: Path) -> list[str]:
    """The frozen registration's ordered instance ids (ordering is authoritative)."""
    data = json.loads(Path(ordered_metadata).read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise AcquisitionError("ordered metadata is not a non-empty list")
    order: list[str] = []
    for i, entry in enumerate(data):
        tid = str(entry.get("instance_id") or "")
        if not tid:
            raise AcquisitionError(f"ordered metadata entry {i} has no instance_id")
        order.append(tid)
    if len(set(order)) != len(order):
        raise AcquisitionError("ordered metadata contains duplicate instance ids")
    return order


# ---------------------------------------------------------------------------
# Task resolution
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TaskInputs:
    """One resolved, registration-validated task. Nothing here is guessed."""

    instance_id: str
    source: str
    source_digest: str
    repo: str
    repo_url: str
    base_commit: str
    test_cmd: str
    fail_to_pass: tuple[str, ...]
    pass_to_pass: tuple[str, ...]
    gold_patch: str
    test_patch: str
    gold_patch_sha256: str
    test_patch_sha256: str
    adjustments: Mapping[str, Any] = field(default_factory=dict)

    def identity_payload(self) -> dict[str, Any]:
        return {
            "schema": ACQUISITION_SCHEMA,
            "registration_id": REGISTRATION_ID,
            "instance_id": self.instance_id,
            "source": self.source,
            "source_digest": self.source_digest,
            "repo": self.repo,
            "base_commit": self.base_commit,
            "test_cmd": self.test_cmd,
            "fail_to_pass": list(self.fail_to_pass),
            "pass_to_pass": list(self.pass_to_pass),
            "gold_patch_sha256": self.gold_patch_sha256,
            "test_patch_sha256": self.test_patch_sha256,
            "adjustments": dict(self.adjustments),
            "builder": BUILDER_SOURCE,
        }

    @property
    def identity(self) -> str:
        return _sha256_bytes(_canonical(self.identity_payload()))


def repo_url_for(repo: str) -> str:
    """``owner/name`` -> the canonical clone URL. Refuses anything else."""
    if not isinstance(repo, str) or repo.count("/") != 1 or " " in repo.strip():
        raise AcquisitionError(f"repository identity is not 'owner/name': {repo!r}")
    owner, name = repo.split("/")
    if not owner or not name:
        raise AcquisitionError(f"repository identity is incomplete: {repo!r}")
    return f"https://github.com/{owner}/{name}.git"


def _canonical_row(source_key: str, raw: Mapping[str, Any]) -> dict[str, Any]:
    """The canonical screening row, via the repository's OWN source adapter.

    Reusing ``f2_population_build.source_to_screen_row`` is not a convenience:
    the two sources declare their test command in different places --
    SWE-bench-Live in ``test_cmds`` and SWE-rebench-V2 in
    ``install_config.test_cmd`` -- and reading the wrong one silently classifies
    every rebench task as "no declared command".  One adapter, one meaning.
    """
    return source_to_screen_row(source_key, raw)


def _pick_test_cmd(value: Any) -> str:
    """Kept for callers that already hold a canonical ``test_cmd`` string."""
    return str(value or "").strip()


def scan_cross_source_conflicts(
    acquired_paths: Mapping[str, Path],
) -> dict[str, str]:
    """Instance ids present in TWO sources whose canonical records DISAGREE.

    The union step already de-duplicated the sources for the registration, so
    these are rare.  They are reported rather than resolved: preferring one
    source would be a guess about which commit and which patches the task means,
    which is exactly the kind of silent substitution this layer must not do.
    A caller selects around them explicitly (``select_tasks(exclude=...)``) and
    the ids stay visible as a recorded, governance-visible gap.

    Returns ``instance_id -> reason``.
    """
    seen: dict[str, tuple[str, bytes, str]] = {}
    conflicts: dict[str, str] = {}
    for source_key in acquired_paths:
        if source_key not in SOURCES:
            raise AcquisitionError(
                f"unknown source {source_key!r}; known: {sorted(SOURCES)}")
    for source_key, path in acquired_paths.items():
        p = Path(path)
        if not p.is_file():
            raise AcquisitionError(f"corpus file missing: {p}")

        def _cmp(d: Mapping[str, Any]) -> bytes:
            # ``_source`` is provenance, not task metadata, so it is excluded.
            return _canonical({k: v for k, v in d.items()
                               if k not in ("_patch", "_source")})

        for row in _iter_acquired(p):
            task_id = str(row.get("instance_id") or "")
            if not task_id:
                continue
            canonical = _canonical_row(source_key, row)
            # Keep only digests, never the rows: the corpus is ~3 GB and a single
            # PASS_TO_PASS list can be 500 KB.
            this = (_cmp(canonical),
                    _sha256_bytes(str(canonical.get("_patch") or "").encode("utf-8")))
            prior = seen.get(task_id)
            if prior is None:
                seen[task_id] = (source_key, this[0], this[1])
                continue
            if prior[1] != this[0]:
                conflicts[task_id] = (
                    f"canonical records disagree between {prior[0]} and {source_key}")
            elif prior[2] and this[1] and prior[2] != this[1]:
                conflicts[task_id] = (
                    f"gold patches differ between {prior[0]} and {source_key}")
    return conflicts


def resolve_task(row: Mapping[str, Any], *, registration_id: str = REGISTRATION_ID
                 ) -> TaskInputs:
    """Validate one corpus row against the frozen registration; refuse anything else.

    ``row`` is a RAW acquired row carrying ``_source`` (added by
    :func:`load_corpus_rows`).  Canonical fields come from the repository's source
    adapter; the patches are read verbatim from the raw row because the screening
    adapter deliberately keeps only ``patch``.
    """
    if registration_id != REGISTRATION_ID:
        raise AcquisitionError(
            f"unknown registration {registration_id!r}; operative is {REGISTRATION_ID!r}")
    source_key = str(row.get("_source") or "")
    if source_key not in SOURCES:
        raise AcquisitionError(f"row carries no known source key: {source_key!r}")
    canonical = _canonical_row(source_key, row)
    task_id = str(canonical.get("instance_id") or "")
    if not task_id:
        raise AcquisitionError("record has no instance_id")
    repo = str(canonical.get("repo") or "")
    base_commit = str(canonical.get("base_commit") or "")
    if len(base_commit) != 40 or not re.fullmatch(r"[0-9a-f]{40}", base_commit):
        raise AcquisitionError(
            f"{task_id}: base_commit is not an immutable 40-hex revision: {base_commit!r}")
    test_cmd = _pick_test_cmd(canonical.get("test_cmd"))
    f2p = [str(v) for v in (canonical.get("fail_to_pass") or [])]
    p2p = [str(v) for v in (canonical.get("pass_to_pass") or [])]
    if not is_pytest_command(test_cmd):
        raise AcquisitionError(
            f"{task_id}: the declared test_cmd is not driveable "
            f"({test_cmd!r}); this source declares log-parser outcomes, not pytest nodes")
    if not eligible(test_cmd, f2p, p2p):
        raise AcquisitionError(
            f"{task_id}: not eligible under the frozen registration "
            f"(evaluator-compatible + python-confirmed)")
    gold = canonical.get("_patch") or row.get("patch")
    test = row.get("test_patch")
    if not isinstance(gold, str) or not gold.strip():
        raise AcquisitionError(f"{task_id}: no gold patch")
    if not isinstance(test, str) or not test.strip():
        raise AcquisitionError(f"{task_id}: no test patch")
    return TaskInputs(
        instance_id=task_id,
        source=source_key,
        source_digest=str(row.get("_source_digest") or ""),
        repo=repo,
        repo_url=repo_url_for(repo),
        base_commit=base_commit,
        test_cmd=test_cmd,
        fail_to_pass=tuple(f2p),
        pass_to_pass=tuple(p2p),
        gold_patch=gold,
        test_patch=test,
        gold_patch_sha256=_sha256_bytes(gold.encode("utf-8")),
        test_patch_sha256=_sha256_bytes(test.encode("utf-8")),
        adjustments=dict(DECLARED_ADJUSTMENTS.get(task_id, {})),
    )


def select_tasks(
    order: Sequence[str],
    rows: Mapping[str, Mapping[str, Any]],
    *,
    limit: int,
    exclude: Iterable[str] = (),
    registration_id: str = REGISTRATION_ID,
) -> list[TaskInputs]:
    """Deterministically take the next ``limit`` registered, eligible tasks.

    Order comes from the registration, never from the corpus.  ``exclude`` is the
    set already recorded in the ledger(s), so a batch resumes instead of
    repeating.  A registered id absent from the corpus is a hard error: the
    registration and the corpus must agree, and "not in this file" must not be
    read as "not eligible".
    """
    if limit <= 0:
        raise AcquisitionError("limit must be positive")
    skip = set(exclude)
    out: list[TaskInputs] = []
    for task_id in order:
        if len(out) >= limit:
            break
        if task_id in skip:
            continue
        row = rows.get(task_id)
        if row is None:
            raise AcquisitionError(
                f"registered task {task_id!r} is absent from the corpus; the "
                "corpus does not cover the operative registration")
        out.append(resolve_task(row, registration_id=registration_id))
    return out


# ---------------------------------------------------------------------------
# Static metadata inspection (no code execution)
# ---------------------------------------------------------------------------
def _read_pyproject(project: Path) -> dict[str, Any]:
    f = project / "pyproject.toml"
    if not f.is_file():
        return {}
    try:
        return tomllib.loads(f.read_text(encoding="utf-8", errors="replace"))
    except Exception:  # noqa: BLE001 - a malformed pyproject is a refusal, not a guess
        return {}


def discover_project_root(tree: Path) -> Path:
    """The directory holding the project's build metadata.

    Deterministic: ``pyproject.toml``, else ``setup.py``, else ``setup.cfg``,
    searched at the tree root and one level down.  More than one candidate at the
    same depth is an ambiguity and is refused, because guessing here silently
    installs the wrong tree.
    """
    tree = Path(tree)
    if not tree.is_dir():
        raise AcquisitionError(f"tree does not exist: {tree}")
    for marker in ("pyproject.toml", "setup.py", "setup.cfg"):
        if (tree / marker).is_file():
            return tree
    found: list[Path] = []
    for child in sorted(p for p in tree.iterdir() if p.is_dir()):
        if any((child / m).is_file()
               for m in ("pyproject.toml", "setup.py", "setup.cfg")):
            found.append(child)
    if len(found) == 1:
        return found[0]
    if not found:
        raise AcquisitionError(f"no project metadata (pyproject/setup) under {tree}")
    raise AcquisitionError(
        f"ambiguous project root under {tree}: {[p.name for p in found]}")


def requires_python(project: Path) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """Declared ``requires-python`` bounds, read statically (or ``None``)."""
    data = _read_pyproject(Path(project))
    spec = str((data.get("project") or {}).get("requires-python") or "")
    if not spec:
        f = Path(project) / "setup.cfg"
        if f.is_file():
            m = re.search(r"python_requires\s*=\s*(.+)",
                          f.read_text(encoding="utf-8", errors="replace"))
            spec = m.group(1).strip() if m else ""
    if not spec:
        return None
    upper = re.search(r"<\s*(\d+)\.(\d+)", spec)
    lower = re.search(r">=\s*(\d+)\.(\d+)", spec)
    lo = (int(lower.group(1)), int(lower.group(2))) if lower else (3, 0)
    hi = (int(upper.group(1)), int(upper.group(2))) if upper else (99, 0)
    return lo, hi


def select_interpreter(
    project: Path,
    candidates: Sequence[Path | str],
    *,
    version_of: Any = None,
) -> str:
    """First candidate satisfying the project's own ``requires-python``.

    ``version_of`` is injected (``path -> (major, minor)``) so selection is
    testable without executing an interpreter.
    """
    bounds = requires_python(project)
    getter = version_of
    for candidate in candidates:
        c = Path(candidate)
        if not c.is_file():
            continue
        if bounds is None:
            return str(c)
        if getter is None:
            raise AcquisitionError("version_of is required when requires-python is declared")
        ver = tuple(getter(str(c)))
        if bounds[0] <= ver < bounds[1]:
            return str(c)
    raise AcquisitionError(
        f"no available interpreter satisfies requires-python={bounds!r}: {list(candidates)}")


def dependency_inputs_from_pyproject(
    data: Mapping[str, Any],
    *,
    requirements_names: Sequence[str] = (),
) -> dict[str, Any]:
    """The canonical shape of a project's static dependency declarations.

    One extractor for BOTH readers of ``pyproject.toml`` -- a checked-out tree
    (:func:`static_dependency_inputs`) and a git object that has never been
    materialised (:mod:`qwen_train.f2_s8_plugin_compat`).  Splitting these two
    is how a classifier drifts from the builder, so they share this function.
    """
    build = [str(v) for v in ((data.get("build-system") or {}).get("requires") or [])]
    # Entries are preserved EXACTLY as declared (a PEP 735 ``include-group``
    # table stays a table): stringifying them would destroy the very structure
    # group resolution needs, and would make the builder's view differ from the
    # classifier's.
    extras = {str(k): list(v or [])
              for k, v in ((data.get("project") or {}).get("optional-dependencies") or {}).items()}
    groups = {str(k): list(v or [])
              for k, v in (data.get("dependency-groups") or {}).items()}
    return {
        "build_requires": build,
        "extras": extras,
        "groups": groups,
        "requirements_names": [str(n) for n in requirements_names if str(n)],
    }


def static_dependency_inputs(project: Path) -> dict[str, Any]:
    """Dependency inputs discoverable WITHOUT executing anything.

    Reads the project's own declarations: ``[build-system].requires``,
    ``[project.optional-dependencies]`` (extras), ``[dependency-groups]``
    (PEP 735) and the requirement *names* in ``requirements*.txt``.  Names only --
    no versions are invented, and no command is run.
    """
    project = Path(project)
    req_names: list[str] = []
    for f in sorted(project.glob("requirements*.txt")):
        req_names.extend(parse_requirements_names(
            f.read_text(encoding="utf-8", errors="replace")))
    return dependency_inputs_from_pyproject(
        _read_pyproject(project), requirements_names=req_names)


def parse_requirements_names(text: str) -> list[str]:
    """Distribution names declared by ONE requirements file's text.

    The shared reader for ``requirements*.txt``: comments stripped, option
    lines (``-r``/``-e``/...) skipped, names split off specifiers, extras,
    markers and direct references -- the exact semantics
    :func:`static_dependency_inputs` has always used, extracted so the offline
    classifier can read a requirements file out of a git object through the
    SAME code instead of a second copy that could drift.
    """
    names: list[str] = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line and not line.startswith("-"):
            names.append(re.split(r"[<>=!~\[; ]", line, maxsplit=1)[0].strip())
    return names


def runtime_dependencies_from_pyproject(data: Mapping[str, Any]) -> list[str]:
    """``[project].dependencies`` verbatim -- the project install brings these in.

    Deliberately NOT a key of ``dependency_inputs``: that mapping is folded into
    the build identity, whose recorded values for the terminal tasks were
    computed over its four existing keys.  Adding a key would silently
    invalidate those recorded identities, which the evidence rules forbid, so
    this category is carried by the install plan instead (where it is actually
    decided) rather than by the identity.
    """
    project = data.get("project")
    if not isinstance(project, Mapping):
        return []
    deps = project.get("dependencies")
    if not isinstance(deps, (list, tuple)):
        return []
    return [str(v) for v in deps]


def uv_default_groups_from_pyproject(data: Mapping[str, Any]) -> list[str]:
    """uv's effective default groups (``[tool.uv].default-groups``; default ``dev``).

    Read statically because ``uv sync`` installs default groups and, with
    ``--all-extras``, every extra -- and nothing else.
    """
    tool = data.get("tool")
    uv = tool.get("uv") if isinstance(tool, Mapping) else None
    raw = uv.get("default-groups") if isinstance(uv, Mapping) else None
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, (list, tuple)):
        return [str(v) for v in raw]
    return ["dev"]


def dependency_metadata_status(data: Mapping[str, Any]) -> list[str]:
    """Codes for dependency metadata this layer CANNOT read statically.

    Static reading is complete only for PEP 621 metadata that is *present* and
    *not delegated*: a missing ``[project]`` table, ``[project].dynamic`` and
    setuptools' ``[tool.setuptools.dynamic]`` all mean the dependency set is
    produced by EXECUTED build logic (PEP 517), which this layer never runs.

    A project in that state can still declare a pytest plugin where nothing
    static sees it, so the classifier must report UNKNOWN -- never "no plugins
    declared, therefore compatible".  ``huggingface__datasets-7353`` is a real
    example: its ``pyproject.toml`` carries only ``[tool.ruff]``/``[tool.pytest]``
    config and its metadata comes from ``setup.py``.
    """
    problems: list[str] = []
    project = data.get("project")
    if not isinstance(project, Mapping):
        problems.append("project_table_absent")
        return problems
    if not str(project.get("name") or "").strip():
        problems.append("project_name_missing")
    dynamic = project.get("dynamic")
    if isinstance(dynamic, (list, tuple)):
        keys = {str(k) for k in dynamic}
        if "dependencies" in keys:
            problems.append("dynamic_project_dependencies")
        if "optional-dependencies" in keys:
            problems.append("dynamic_project_optional_dependencies")
    tool = data.get("tool")
    setuptools = tool.get("setuptools") if isinstance(tool, Mapping) else None
    sdynamic = setuptools.get("dynamic") if isinstance(setuptools, Mapping) else None
    if isinstance(sdynamic, Mapping):
        if "dependencies" in sdynamic:
            problems.append("dynamic_setuptools_dependencies")
        if "optional-dependencies" in sdynamic:
            problems.append("dynamic_setuptools_optional_dependencies")
    return problems


def install_context_from_pyproject(
    data: Mapping[str, Any],
    *,
    requirements_names: Sequence[str] = (),
) -> dict[str, Any]:
    """Everything the install plan needs, from ONE parsed ``pyproject.toml``.

    Both readers -- a checked-out tree (:func:`project_install_context`) and a
    git object that was never materialised (:mod:`f2_s8_plugin_compat`) -- land
    here, so the builder and the classifier cannot disagree about a project.
    """
    return {
        "dependency_inputs": dependency_inputs_from_pyproject(
            data, requirements_names=requirements_names),
        "runtime_dependencies": runtime_dependencies_from_pyproject(data),
        "uv_default_groups": uv_default_groups_from_pyproject(data),
        "metadata_status": dependency_metadata_status(data),
    }


def project_install_context(project: Path) -> dict[str, Any]:
    """:func:`install_context_from_pyproject` for a checked-out project tree.

    ``dependency_inputs`` comes from :func:`static_dependency_inputs` itself --
    not from a second copy of the ``requirements*.txt`` reader -- so the plan the
    builder records is built from EXACTLY the mapping its build identity uses.
    """
    project = Path(project)
    context = install_context_from_pyproject(_read_pyproject(project))
    context["dependency_inputs"] = static_dependency_inputs(project)
    return context


# ---------------------------------------------------------------------------
# Build identity
# ---------------------------------------------------------------------------
def materialise_task_inputs(task: TaskInputs, *, root: Path) -> dict[str, Any]:
    """Write a resolved task's patches to disk and compute its build identity.

    This is the pure half of the integration: no network, no clone, no install,
    no task command.  The clone/venv work (which necessarily executes the
    project's PEP 517 build backend) belongs to the acquisition/build boundary and
    is driven separately, so this half stays testable and side-effect-free apart
    from the two patch files.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    gold = root / f"{task.instance_id}.gold_patch"
    test = root / f"{task.instance_id}.test_patch"
    gold.write_bytes(task.gold_patch.encode("utf-8"))
    test.write_bytes(task.test_patch.encode("utf-8"))
    if _sha256_file(gold) != task.gold_patch_sha256:
        raise AcquisitionError(f"{task.instance_id}: gold patch digest mismatch on disk")
    if _sha256_file(test) != task.test_patch_sha256:
        raise AcquisitionError(f"{task.instance_id}: test patch digest mismatch on disk")
    return {
        "task_id": task.instance_id,
        "repo_url": task.repo_url,
        "base_commit": task.base_commit,
        "test_cmd": task.test_cmd,
        "gold_patch": str(gold),
        "test_patch": str(test),
        "source": task.source,
        "source_digest": task.source_digest,
        "adjustments": dict(task.adjustments),
        "task_identity": task.identity,
    }


def build_identity_for_tree(
    task: TaskInputs,
    tree: Path,
    *,
    interpreter: str,
    builder_digest: str,
) -> str:
    """The build identity of a materialised tree: project root + static deps included."""
    project = discover_project_root(Path(tree))
    return build_identity(
        task,
        project_root=str(project),
        interpreter=interpreter,
        dependency_inputs=static_dependency_inputs(project),
        builder_digest=builder_digest,
    )


def normalize_distribution_name(name: str) -> str:
    """PEP 503 normalised distribution name, via ``packaging`` (declared dep).

    ``pytest_django`` / ``pytest.django`` / ``pytest-Django`` are ONE project.
    Comparing raw spellings is how a correct declaration silently escapes a
    prefix test, so every comparison in this layer goes through this function.
    """
    return canonicalize_name(str(name).strip())


def _requirement(raw: str) -> Requirement:
    """Parse ONE PEP 508 declaration with ``packaging``; ``ValueError`` if it is not one.

    Single entry point for every parse in this module: splitting on punctuation
    silently mis-reads specifiers, extras, markers and direct references
    (``pkg[a]>=1; python_version<"3.9"`` and ``pkg @ https://…`` both survive).
    """
    text = str(raw).strip()
    try:
        return Requirement(text)
    except InvalidRequirement as exc:
        raise ValueError(str(exc)) from exc


def _requirement_name(raw: str) -> str:
    """Canonical (PEP 503) distribution name of a declaration."""
    return canonicalize_name(_requirement(raw).name)


def _constraints_conflict(spec_set: SpecifierSet) -> str | None:
    """Reason no single version can satisfy ``spec_set``, or ``None``.

    Interval reasoning over the declared bounds -- equality pins become a
    point, ``>=``/``>`` the lower end, ``<``/``<=`` the upper end.  This is
    deliberately narrow: it reports a conflict only when the bounds are
    provably empty, so a satisfiable pair (``>5,<6``) is never rejected, and
    anything it cannot decide is left to pip, which fails the build loudly
    rather than dropping a constraint.
    """
    lower_v: Version | None = None
    lower_strict = False
    upper_v: Version | None = None
    upper_strict = False
    for spec in spec_set:
        try:
            v = Version(spec.version)
        except InvalidVersion:
            return None  # not PEP 440-comparable: no claim, pip decides
        op = spec.operator
        if op in (">=", ">"):
            if lower_v is None or v > lower_v or (v == lower_v and op == ">"):
                lower_v, lower_strict = v, op == ">"
        elif op in ("<", "<="):
            if upper_v is None or v < upper_v or (v == upper_v and op == "<"):
                upper_v, upper_strict = v, op == "<"
        elif op == "==" and not spec.version.endswith(".*"):
            # an equality pin is a point: intersect it with the other bounds
            if lower_v is None or v > lower_v:
                lower_v, lower_strict = v, False
            if upper_v is None or v < upper_v:
                upper_v, upper_strict = v, False
    if lower_v is None or upper_v is None:
        return None
    if lower_v > upper_v:
        return f"no version satisfies lower {lower_v} and upper {upper_v}"
    if lower_v == upper_v and (lower_strict or upper_strict):
        return f"no version satisfies {'>' if lower_strict else '<'}{lower_v} tightly"
    return None


def _combine_declarations(
    name: str,
    entries: Sequence[tuple[str, str]],
) -> tuple[str | None, str | None]:
    """AND every distinct declaration of ONE distribution into one pip argument.

    ``entries`` are ``(source, raw)`` pairs.  Returns ``(combined, conflict)``.

    pip refuses a doubled requirement for one distribution, so the alternatives
    are to AND them or to refuse -- never to keep the first and **silently drop
    the other** (which is how a ``<5`` ceiling can vanish from an install).

    * one distinct declaration -> returned verbatim, specifier and marker
      untouched, so the project's own constraint stays in force;
    * several specifier-only declarations -> ANDed into a single requirement;
    * declarations that cannot be safely ANDed (a marker or direct reference
      alongside another declaration of the same distribution, or bounds no
      version can satisfy) -> ``(None, <reason>)`` so the caller fails closed.
    """
    raws = sorted({raw for _, raw in entries})
    if len(raws) == 1:
        return raws[0], None
    parsed: list[Requirement] = []
    for raw in raws:
        try:
            parsed.append(_requirement(raw))
        except ValueError:
            return None, f"{name}: unparsable declaration {raw!r}"
    if any(r.marker is not None or r.url for r in parsed):
        return None, (f"{name}: cannot combine conditional/direct declarations {raws}")
    spec_set = SpecifierSet(",".join(
        str(r.specifier) for r in parsed if str(r.specifier)))
    conflict = _constraints_conflict(spec_set)
    if conflict:
        return None, f"{name}: {conflict} ({raws})"
    extras = sorted({e for r in parsed for e in r.extras})
    base = name + (f"[{','.join(extras)}]" if extras else "")
    return (base + str(spec_set)) if str(spec_set) else base, None


def resolve_dependency_groups(
    groups: Mapping[str, Any],
) -> tuple[dict[str, list[Any]], list[dict[str, str]]]:
    """Expand PEP 735 ``include-group`` directives recursively, cycle-safe.

    Returns ``(expanded, problems)``.  Every entry of a declared group is either
    a requirement string or ``{"include-group": "<name>"}``; the directive is
    resolved against the project's OWN groups, and a missing group, a cycle, or
    an entry that is neither form is reported rather than guessed around.
    """
    problems: list[dict[str, str]] = []
    expanded: dict[str, list[Any]] = {}
    resolving: list[str] = []

    def _expand(name: str) -> list[Any]:
        if name in expanded:
            return expanded[name]
        if name in resolving:
            cycle = " -> ".join([*resolving, name])
            problems.append({"code": "dependency_group_cycle", "scope": "blocking",
                             "detail": cycle})
            return []
        if name not in groups:
            problems.append({"code": "dependency_group_missing", "scope": "blocking",
                             "detail": name})
            return []
        resolving.append(name)
        out: list[Any] = []
        for entry in groups[name] or ():
            if isinstance(entry, Mapping):
                target = entry.get("include-group")
                if target is None:
                    problems.append({"code": "unsupported_group_entry", "scope": "blocking",
                                     "detail": f"{name}: {sorted(entry)}"})
                    continue
                if set(entry) != {"include-group"}:
                    problems.append({"code": "unsupported_group_entry", "scope": "blocking",
                                     "detail": f"{name}: {sorted(entry)}"})
                out.extend(_expand(str(target)))
            elif not str(entry).strip():
                problems.append({"code": "empty_requirement", "scope": "blocking",
                                 "detail": name})
            else:
                out.append(entry)
        resolving.pop()
        expanded[name] = out
        return out

    for key in sorted(groups):
        _expand(key)
    return expanded, problems


def declared_test_plugins_report(
    dependency_inputs: Mapping[str, Any],
    *,
    include_extras: bool = False,
    runtime_dependencies: Sequence[str] = (),
) -> dict[str, Any]:
    """The project's OWN declared pytest plugins, with evidence and problems.

    Why this exists (defect, Sessions 40b/41)
    -----------------------------------------
    ``pytest.ini_options.DJANGO_SETTINGS_MODULE`` is honoured only by
    ``pytest-django``; pytest itself ignores it.  A project that declares the
    plugin in ``[dependency-groups]`` (PEP 735) but whose environment installs
    only ``pytest`` fails COLLECTION with ``django.core.exceptions.
    ImproperlyConfigured: settings are not configured``, so every declared node
    is absent from the JUnit and the task can never resolve.  That is exactly
    what happened to the two django-guardian tasks.

    Selection rules (static; nothing is invented)
    ---------------------------------------------
    * declarations are parsed with ``packaging`` (PEP 508), not split on
      punctuation -- specifiers, extras, environment markers and direct
      references are all read as written;
    * names are PEP 503 normalised before any comparison;
    * ``include-group`` is expanded by :func:`resolve_dependency_groups`;
    * a distribution counts as a pytest plugin when its canonical name starts
      with ``pytest-`` (pytest's own plugin naming convention) -- recorded per
      plugin as ``evidence: "name_convention"`` so the basis is auditable;
    * only ``dependency-groups`` are in scope unless ``include_extras`` says the
      build contract actually selects extras (uv: ``--all-extras``; pip
      ``-e .``: no extras -- "uv does not sync extras by default", and
      ``pip install -e .`` installs ``[project].dependencies`` only).

    Returns ``{"plugins": {name: {...}}, "problems": [...]}``.  Declarations are
    reported per source and per plugin; combining several declarations of one
    distribution into a single install argument is the PLAN's job
    (:func:`builder_install_plan`), so nothing is merged (or dropped) here.
    ``problems`` carries a ``scope``: ``blocking`` (the structure itself cannot
    be trusted) or ``offline`` (parseable, but not verifiable offline).

    ``runtime_dependencies`` is a distinct source category: ``[project].
    dependencies`` really does land in the environment -- ``pip install -e .``
    and ``uv sync`` both install it -- unlike ``[build-system].requires``
    (consumed by PEP 517 build isolation, never left in the venv) and unlike
    ``requirements*.txt`` (this builder never runs ``pip install -r``), neither
    of which is ever read here as installed.

    ``requirements*.txt`` is nevertheless a DECLARED source (Session 42): a
    pytest plugin named in a root requirements file is a plugin the project
    declares and NO contract installs, so it must appear here and reach the
    classifier as a mismatch -- never be invisible and let a task be certified
    ``OBSERVED_COMPATIBLE`` while its own requirements file asks for a plugin
    the environment provably lacks.  Its entries can never be BLOCKING (an
    unparsable name is ``offline``-scoped): unlike a group entry, a requirements
    entry takes part in no install this plan performs, so it must not be able
    to break one.
    """
    report_problems: list[dict[str, str]] = []
    expanded, group_problems = resolve_dependency_groups(
        dependency_inputs.get("groups") or {})
    report_problems.extend(group_problems)

    plugins: dict[str, dict[str, Any]] = {}

    def _consider(raw: Any, where: str, kind: str) -> None:
        if isinstance(raw, Mapping):  # already reported by resolve_dependency_groups
            return
        text = str(raw).strip()
        if not text:
            report_problems.append({"code": "empty_requirement", "scope": "blocking",
                                    "detail": where})
            return
        try:
            req = _requirement(text)
        except ValueError:
            # Only a declaration that COULD be a pytest plugin may block the
            # build: this plan installs nothing else from a group, so a
            # non-standard entry (a local path, a uv-style ``-e ./lib``) must
            # not break an install it does not take part in.  It is still
            # recorded, so the classifier reports UNKNOWN rather than treating
            # an unreadable declaration as "no plugin declared".
            #
            # A requirements entry is NEVER blocking, however: the builder does
            # not install ``requirements*.txt`` at all, so nothing there can
            # change an install -- it can only change a classification.
            if kind == "requirements":
                scope = "offline"
            else:
                scope = "blocking" if "pytest" in text.lower() else "offline"
            report_problems.append({"code": "unparsable_requirement", "scope": scope,
                                    "detail": f"{kind}:{where}: {text}"})
            return
        name = canonicalize_name(req.name)
        if not name.startswith("pytest-"):
            # Not a pytest plugin by pytest's own naming convention, and NOT
            # installed from a group by this builder -- collecting it here is
            # how non-plugins leak into a plugin classification.
            return
        source = f"{kind}:{where}" if kind != "runtime" else "runtime:[project].dependencies"
        entry = plugins.setdefault(name, {
            "name": name,
            # pytest's own plugin naming convention; recorded so the basis of the
            # classification is auditable rather than assumed.
            "evidence": "name_convention",
            "declarations": [],
            "sources": [],
            "by_source": {},
            "conditional": False,
            "direct_reference": False,
        })
        if text not in entry["declarations"]:
            entry["declarations"].append(text)
        if source not in entry["sources"]:
            entry["sources"].append(source)
        bucket = entry["by_source"].setdefault(kind, [])
        if text not in bucket:
            bucket.append(text)
        if req.marker is not None:
            entry["conditional"] = True
            report_problems.append({"code": "conditional_declaration", "scope": "offline",
                                    "detail": f"{source}: {text}"})
        if req.url:
            entry["direct_reference"] = True
            report_problems.append({"code": "direct_reference", "scope": "offline",
                                    "detail": f"{source}: {text}"})

    for group_name in sorted(expanded):
        for entry in expanded[group_name]:
            _consider(entry, group_name, "group")
    if include_extras:
        for extra_name in sorted(dependency_inputs.get("extras") or {}):
            for entry in (dependency_inputs["extras"] or {})[extra_name] or ():
                _consider(entry, extra_name, "extra")
    for entry in runtime_dependencies or ():
        _consider(entry, "dependencies", "runtime")
    # ``requirements*.txt``: a DECLARED source no contract ever installs (the
    # builder never runs ``pip install -r``).  PEP 508-parsable pytest plugin
    # names surface as declarations here, so an omitted plugin cannot hide
    # behind "the project declares nothing".
    for entry in dependency_inputs.get("requirements_names") or ():
        _consider(entry, "requirements*.txt", "requirements")

    return {"plugins": plugins, "expanded_groups": expanded, "problems": report_problems}


def declared_test_plugins(
    dependency_inputs: Mapping[str, Any],
    *,
    include_extras: bool = False,
    runtime_dependencies: Sequence[str] = (),
) -> list[str]:
    """Normalised names of the declared pytest plugins (see the report fn)."""
    return sorted(declared_test_plugins_report(
        dependency_inputs,
        include_extras=include_extras,
        runtime_dependencies=runtime_dependencies,
    )["plugins"])


def builder_install_plan(
    dependency_inputs: Mapping[str, Any],
    *,
    pinned: Sequence[str] = (),
    extra_deps: Sequence[str] = (),
    include_extras: bool = False,
    runtime_dependencies: Sequence[str] = (),
    mode: str = "pip",
    uv_default_groups: Sequence[str] = ("dev",),
) -> dict[str, Any]:
    """The packages the pilot builder's environment actually gets -- ONE code path.

    Both the builder (:mod:`qwen_train.f2_s8_pilot_build`) and the offline
    classifier (:mod:`qwen_train.f2_s8_plugin_compat`) read this function, so
    "what will be installed" can never drift from "what is checked".

    Four mechanisms stay distinct, because they are not interchangeable:

    ======================  ====================================================
    ``pinned``             the builder's pinned pytest baseline (explicit arg)
    ``task_extra_deps``    the hand-declared per-task collection requirements
                            (explicit arg)
    ``dependency_group``   PEP 735 group members -- pip mode installs ONLY the
                            declared pytest plugins (narrow by design); uv mode
                            installs the whole of uv's default groups
    ``optional_extra``     only when the contract selects extras (uv:
                            ``uv sync --all-extras``; pip ``-e .``: never)
    ``runtime_dependency`` ``[project].dependencies``, installed by the
                            editable/uv project install itself
    ======================  ====================================================

    NOT mechanisms, and therefore never counted as installed:
    ``[build-system].requires`` (PEP 517 build isolation -- the build
    environment is discarded) and ``requirements*.txt`` (this builder never
    runs ``pip install -r``).  Appearing in project metadata is not evidence of
    installation; only one of the mechanisms above is.

    ``installed_packages`` therefore lists what the contract installs
    DIRECTLY (pinned baseline, task extras, selected group/extra members and
    runtime dependencies).  It is not the transitive closure of those -- that
    needs resolution, and this function resolves nothing.

    Fail-closed: a declaration whose structure cannot be trusted (cycle,
    unsupported entry, unparsable requirement) or whose constraints cannot be
    safely ANDed onto one distribution raises rather than installing one
    constraint and silently dropping the other.  Parseable-but-not-
    offline-verifiable declarations (markers, direct references) are passed to
    pip verbatim -- pip resolves them at build time.
    """
    if mode not in ("pip", "uv"):
        raise AcquisitionError(f"unknown install mode {mode!r}; expected 'pip' or 'uv'")
    if mode == "uv":
        # ``uv sync --frozen --all-extras``: extras are always in scope, and uv
        # -- not pip -- owns that environment, so nothing is shelled out here.
        include_extras = True
    report = declared_test_plugins_report(
        dependency_inputs,
        include_extras=include_extras,
        runtime_dependencies=runtime_dependencies,
    )
    blocking = [p for p in report["problems"] if p["scope"] == "blocking"]
    if blocking:
        raise AcquisitionError(
            "declared dependency groups cannot be resolved: "
            + "; ".join(f"{p['code']}:{p['detail']}" for p in blocking))
    plugins: Mapping[str, Any] = report["plugins"]
    expanded: Mapping[str, Any] = report["expanded_groups"]

    installed: dict[str, set[str]] = {}

    def _install(name: str, mechanism: str) -> str:
        installed.setdefault(name, set()).add(mechanism)
        return name

    def _name(raw: Any, where: str) -> str:
        text = str(raw).strip()
        if not text:
            raise AcquisitionError(f"empty install requirement for {where}")
        try:
            return _requirement_name(text)
        except ValueError as exc:
            raise AcquisitionError(
                f"unparsable install requirement for {where}: {text!r}: {exc}") from None

    # (distribution) -> [(mechanism, raw declaration)] for everything the
    # builder passes to pip itself.  Every declaration of a distribution is
    # kept; none is dropped in favour of another (see _combine_declarations).
    explicit_entries: dict[str, list[tuple[str, str]]] = {}
    plugin_arg_names: set[str] = set()

    if mode == "pip":
        # ONE parse per declaration: ``_name`` already runs ``_requirement`` and
        # returns the PEP 503 canonical name, so re-parsing the same text to
        # canonicalise it again would be a duplicate parse of one declaration.
        for req in pinned:
            text = str(req).strip()
            name = _name(text, "pinned")
            explicit_entries.setdefault(name, []).append(("pinned", text))
            _install(name, "pinned")
        for req in extra_deps:
            text = str(req).strip()
            name = _name(text, "task_extra_deps")
            explicit_entries.setdefault(name, []).append(
                ("task_extra_deps", text))
            _install(name, "task_extra_deps")
        for name in sorted(plugins):
            info = plugins[name]
            for kind, mechanism in (("group", "dependency_group"),
                                    ("extra", "optional_extra"),
                                    ("runtime", "runtime_dependency")):
                for raw in info.get("by_source", {}).get(kind, ()):
                    _install(name, mechanism)
                    if kind == "runtime":
                        continue  # the editable install already brings it
                    explicit_entries.setdefault(name, []).append((mechanism, raw))
                    plugin_arg_names.add(name)
    else:  # uv owns this environment: record what IT installs, shelling out nothing
        wanted = {str(g) for g in uv_default_groups}
        all_groups = "all" in wanted
        for group_name in sorted(expanded):
            if not all_groups and group_name not in wanted:
                continue
            for raw in expanded[group_name]:
                if isinstance(raw, Mapping):
                    continue
                _install(_name(raw, f"group:{group_name}"), "dependency_group")
        for name in sorted(plugins):
            info = plugins[name]
            if info.get("by_source", {}).get("extra"):
                _install(name, "optional_extra")

    # [project].dependencies is installed by the project install itself, in
    # BOTH modes (``pip install -e .`` / ``uv sync`` -- neither uses --no-deps).
    # An entry the report could not read is skipped here rather than raising:
    # the unreadable-declaration problem is already recorded, and the
    # classifier turns any recorded problem into UNKNOWN, never into
    # "compatible".
    for req in runtime_dependencies or ():
        try:
            runtime_name = _name(req, "runtime")
        except AcquisitionError:
            continue
        _install(runtime_name, "runtime_dependency")

    # One distribution, one pip argument -- combined, never first-one-wins.
    args: list[str] = []
    arg_for: dict[str, str] = {}
    for name in sorted(explicit_entries):
        combined, conflict = _combine_declarations(name, explicit_entries[name])
        if conflict:
            raise AcquisitionError(f"conflicting declarations for {name}: {conflict}")
        arg_for[name] = combined
        args.append(combined)

    return {
        "mode": mode,
        "explicit_requirements": args,
        "plugin_requirements": [arg_for[n] for n in sorted(plugin_arg_names)],
        "plugins": sorted(plugins),
        "installed_packages": {n: sorted(m) for n, m in sorted(installed.items())},
        "packages": sorted(installed),
        "include_extras": bool(include_extras),
        "problems": list(report["problems"]),
    }


def build_identity(
    task: TaskInputs,
    *,
    project_root: str,
    interpreter: str,
    dependency_inputs: Mapping[str, Any],
    builder_digest: str,
) -> str:
    """The identity a built workspace is reused under.

    Every input that can change what the guest executes is included, so a changed
    patch, commit, spec, environment input or builder identity can never reuse a
    stale workspace (PEP 517: "the presence or absence of any caches should not
    make a material difference to the final result of the build").
    """
    return _sha256_bytes(_canonical({
        "schema": ACQUISITION_SCHEMA,
        "task": task.identity_payload(),
        "project_root": str(project_root),
        "interpreter": str(interpreter),
        "dependency_inputs": dict(dependency_inputs),
        "builder_digest": str(builder_digest),
    }))
