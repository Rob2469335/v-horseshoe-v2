"""Offline F2 S8 plugin-compatibility classifier (Priority C, Session 42).

What it answers
---------------
For every selectable task: **will the builder's environment contain every pytest
plugin the project itself declares?**  That is the question the two
django-guardian tasks answered "no" to at runtime -- collection failed with
``ImproperlyConfigured: settings are not configured`` because ``pytest-django``
was declared in ``[dependency-groups] dev`` but never installed, so pytest
ignored ``DJANGO_SETTINGS_MODULE`` and every declared node went missing.

Evidence boundaries (these are what make the result admissible)
--------------------------------------------------------------
* READ-ONLY and offline: no clone, no network, no install, no environment, no
  guest, no pilot, no ledger access beyond READING its task ids.
* Metadata comes from local git OBJECTS (``git show <base_commit>:<path>``) --
  never a checkout, never a working-tree mutation.  Both ``pyproject.toml`` and
  any root-level ``requirements*.txt`` are read this way; requirements entries
  are a DECLARED-but-never-installed source (rule v2), so a plugin named only
  there reaches the declared set and surfaces as a mismatch instead of hiding
  behind "the project declares nothing".
* The install set is the builder's OWN plan
  (:func:`qwen_train.f2_s8_acquisition.builder_install_plan`), read from the
  same constants the builder uses (``PYTEST_PIN``, ``EXTRA_DEPS``,
  ``UV_ALL_EXTRAS``), so classifier and builder cannot drift.
* A local clone is not proof of installation, and a declared dependency is not
  proof of installation: only a mechanism in the plan is.
* The classification covers PLUGIN-INSTALL COVERAGE ONLY.  It does not say the
  task builds, that its nodes exist, that its environment works, or that it is
  scientifically analyzable.

Classes
-------
``OBSERVED_COMPATIBLE``    metadata readable, no recorded problem, and the
                           builder's plan installs every declared plugin.
``OBSERVED_INCOMPATIBLE``  metadata declares at least one plugin the plan omits
                           (each mismatch listed with its sources).  A
                           requirements-sourced mismatch is proven only under
                           statically complete metadata; with dynamic/delegated
                           dependency metadata the task is UNKNOWN instead,
                           because setup.py may read requirements.txt into
                           install_requires itself.
``UNKNOWN``                evidence missing, unreadable, ambiguous or
                           unverifiable offline -- reason code recorded.
                           Never silently counted as compatible.
``NOT_APPLICABLE``         the registered declared command is not a pytest
                           command, so a pytest-plugin check cannot apply
                           (rule: :func:`qwen_train.f2_registration.is_pytest_command`).
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import subprocess
import time
import tomllib
from pathlib import Path
from typing import Any, Mapping, Sequence

from qwen_train.f2_registration import is_pytest_command
from qwen_train.f2_s8_acquisition import (
    AcquisitionError,
    builder_install_plan,
    declared_test_plugins_report,
    install_context_from_pyproject,
    load_registration_order,
    parse_requirements_names,
    scan_cross_source_conflicts,
)
from qwen_train.f2_s8_pilot_build import EXTRA_DEPS, GIT, PYTEST_PIN, UV_ALL_EXTRAS

__all__ = [
    "PLUGIN_CHECK_RULE_VERSION",
    "CLASS_COMPATIBLE",
    "CLASS_INCOMPATIBLE",
    "CLASS_UNKNOWN",
    "CLASS_NOT_APPLICABLE",
    "classify_population",
    "main",
]

#: Bumped whenever a classification rule changes, so two reports can be
#: compared only when they were produced by the SAME rule.
#: v2 (Session 42): root ``requirements*.txt`` declarations join the declared
#: set (declared-but-NEVER-installed source).  A pytest plugin named only in a
#: requirements file is therefore OBSERVED_INCOMPATIBLE instead of invisible --
#: v1 let such a task be certified compatible while its own requirements file
#: asked for a plugin the environment provably lacked (2 of its 27 compatible
#: tasks did exactly that).
PLUGIN_CHECK_RULE_VERSION = "f2-plugin-compat-v2"

CLASS_COMPATIBLE = "OBSERVED_COMPATIBLE"
CLASS_INCOMPATIBLE = "OBSERVED_INCOMPATIBLE"
CLASS_UNKNOWN = "UNKNOWN"
CLASS_NOT_APPLICABLE = "NOT_APPLICABLE"
CLASSES = (CLASS_COMPATIBLE, CLASS_INCOMPATIBLE, CLASS_UNKNOWN, CLASS_NOT_APPLICABLE)

REPO = Path(r"C:\Users\rober\Projects\v-horseshoe-v2")
POP = REPO / "data" / "f2_population"
CORPUS: dict[str, Path] = {
    "swe-bench-live": POP / "upstream_swe_bench_live_b51a8642" / "acquired.jsonl",
    "swe-rebench-v2": POP / "upstream_swe_rebench_v2_10483de0" / "acquired.jsonl",
}
ORDER = POP / "registration" / "full_ordered_metadata.json"
SOURCE_MANIFEST = POP / "ordering" / "source_manifest.json"
LEDGER = Path(r"C:\Users\rober\F2_EVIDENCE\s8\trusted_store\s8_pilot_ledger.jsonl")
OUTPUT_DIR = POP / "plugin_compat"

#: Where local task clones may already exist (made by earlier phases; this
#: classifier CREATES none).  Absence is a reason code, never a verdict.
TREE_ROOTS = (
    POP / "work",
    POP / "swe_probe",
    Path(r"C:\f2_pilot"),
)

_MARKERS = ("pyproject.toml", "setup.py", "setup.cfg")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def _git(git_dir: Path, *args: str) -> str:
    """Run one read-only git command in an existing clone (no network, no write)."""
    proc = subprocess.run([GIT, "-C", str(git_dir), *[str(a) for a in args]],
                          capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout


class GitTreeReader:
    """Read files at an exact commit from local objects -- no checkout, no network."""

    def __init__(self, git_dir: Path, commit: str) -> None:
        self.git_dir = Path(git_dir)
        self.commit = str(commit)
        self._names: list[str] | None = None

    @property
    def names(self) -> list[str]:
        if self._names is None:
            self._names = [line for line in
                           _git(self.git_dir, "ls-tree", "-r", "--name-only",
                                self.commit).splitlines() if line]
        return self._names

    def read(self, relpath: str) -> str:
        return _git(self.git_dir, "show", f"{self.commit}:{relpath}")


def _project_root(names: Sequence[str]) -> tuple[str | None, str | None]:
    """Mirror :func:`f2_s8_acquisition.discover_project_root` over a name list.

    Returns ``(root_prefix, reason)``: ``root_prefix`` is "" for the tree root,
    ``"sub/"`` for a one-level sub-project, and ``None`` when the answer is
    unavailable (``reason`` says why) -- never a guess.
    """
    root_files = {n for n in names if "/" not in n}
    if root_files & set(_MARKERS):
        return "", None
    subdirs = {n.split("/", 1)[0] + "/" for n in names
               if "/" in n and n.split("/", 1)[1] in _MARKERS}
    if len(subdirs) == 1:
        return next(iter(subdirs)), None
    if len(subdirs) > 1:
        return None, "project_root_ambiguous"
    return None, "project_root_not_found"


def _find_clone(task_id: str, *, tree_roots: Sequence[Path] = TREE_ROOTS) -> Path | None:
    for root in tree_roots:
        for candidate in (root / task_id, root / task_id / "repo"):
            if (candidate / ".git").exists() or (candidate / "HEAD").is_file():
                return candidate
    return None


def _read_context(reader: GitTreeReader) -> tuple[dict[str, Any] | None, str | None]:
    """Shared install context for one task's project, read from git objects."""
    root, reason = _project_root(reader.names)
    if root is None:
        return None, reason
    if f"{root}pyproject.toml" not in set(reader.names):
        # A setup.py-only project delegates dependency discovery to executed
        # build logic; nothing here may execute it.
        return None, "pyproject_absent"
    try:
        data = tomllib.loads(reader.read(f"{root}pyproject.toml"))
    except (tomllib.TOMLDecodeError, RuntimeError):
        return None, "pyproject_unparseable"
    # Root-level ``requirements*.txt`` read through the SAME parser the
    # builder's ``static_dependency_inputs`` uses (fnmatch mirrors pathlib's
    # case-normalised matching; sorted mirrors its order).  Declared-only:
    # no contract installs these, but a pytest plugin named in one must reach
    # the declared set, or a requirements-declared plugin would read as "the
    # project declares nothing" and the task would be certified compatible
    # while the environment provably lacks it (rule v2).
    req_names: list[str] = []
    req_files = sorted(
        n[len(root):] for n in reader.names
        if n.startswith(root) and "/" not in n[len(root):]
        and fnmatch.fnmatch(n[len(root):], "requirements*.txt"))
    try:
        for rel in req_files:
            req_names.extend(parse_requirements_names(reader.read(f"{root}{rel}")))
    except RuntimeError:
        return None, "requirements_unreadable"
    return install_context_from_pyproject(data, requirements_names=req_names), None


def _ledger_task_ids(ledger: Path) -> tuple[list[str], int]:
    """``(distinct task ids in registration order, row count)`` -- READ ONLY."""
    if not ledger.is_file():
        return [], 0
    rows = [json.loads(line) for line in
            ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
    ordered: list[str] = []
    for row in rows:
        task_id = str(row.get("task_id") or "")
        if task_id and task_id not in ordered:
            ordered.append(task_id)
    return ordered, len(rows)


def _slim_rows(selectable: set[str]) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Stream both corpora ONCE: per selectable task, the fields classification needs.

    ``instance_id -> {"source", "test_cmd", "base_commit", "repo"}`` plus the
    SHA-256 of each corpus file (computed in the same pass, so provenance costs
    no extra read).  One row is held at a time: the corpus is ~3 GB and a single
    PASS_TO_PASS list can be 500 KB.
    """
    from qwen_train.f2_s8_acquisition import _canonical_row, _iter_acquired
    from qwen_train.f2_population_build import SOURCE_PRIORITY

    def _rank(source_key: str) -> int:
        return (SOURCE_PRIORITY.index(source_key) if source_key in SOURCE_PRIORITY
                else len(SOURCE_PRIORITY))

    out: dict[str, dict[str, Any]] = {}
    digests: dict[str, str] = {}
    for source_key, path in CORPUS.items():
        digest = hashlib.sha256()
        with Path(path).open("rb") as raw:
            for chunk in iter(lambda: raw.read(1 << 22), b""):
                digest.update(chunk)
        digests[source_key] = digest.hexdigest()
        for row in _iter_acquired(path):
            task_id = str(row.get("instance_id") or "")
            if task_id not in selectable:
                continue
            # The OPERATIVE row is the one the existing `SOURCE_PRIORITY` policy
            # selects: keep the highest-priority source's row.  A plain
            # ``out[task_id] = ...`` let the LAST source iterated (SWE-rebench-V2,
            # the lower priority) overwrite the live row, so a cross-source id
            # would have been classified from the source the union did NOT keep.
            prior = out.get(task_id)
            if prior is not None and _rank(prior["source"]) <= _rank(source_key):
                continue
            canonical = _canonical_row(source_key, row)
            out[task_id] = {
                "source": source_key,
                "test_cmd": str(canonical.get("test_cmd") or ""),
                "base_commit": str(canonical.get("base_commit") or ""),
                "repo": str(canonical.get("repo") or ""),
            }
    return out, digests


def _classify_one(
    task_id: str,
    meta: Mapping[str, Any],
    *,
    tree_roots: Sequence[Path] = TREE_ROOTS,
) -> dict[str, Any]:
    """Classify ONE selectable task.  Every non-A result carries a reason code."""
    result: dict[str, Any] = {
        "task_id": task_id,
        "source": meta.get("source"),
        "test_cmd": meta.get("test_cmd"),
        "classification": CLASS_UNKNOWN,
        "reasons": [],
        "declared_plugins": [],
        "installed_plugins": [],
        "missing_plugins": [],
        "mismatches": [],
        "evidence": {},
    }
    if not is_pytest_command(meta.get("test_cmd")):
        result.update(
            classification=CLASS_NOT_APPLICABLE,
            reasons=["declared_command_not_pytest"],
        )
        return result

    clone = _find_clone(task_id, tree_roots=tree_roots)
    if clone is None:
        result["reasons"] = ["project_tree_unavailable"]
        return result
    commit = str(meta.get("base_commit") or "")
    try:
        reader = GitTreeReader(clone, commit)
        context, reason = _read_context(reader)
    except (RuntimeError, AcquisitionError, OSError):
        result["reasons"] = ["base_commit_object_unavailable"]
        return result
    if context is None:
        result["reasons"] = [reason or "metadata_unavailable"]
        return result
    result["evidence"]["project_root_git_dir"] = str(clone)
    result["evidence"]["commit"] = commit

    deps = context["dependency_inputs"]
    runtime = context["runtime_dependencies"]
    # Reference set: EVERY plugin the project declares, wherever it declares it.
    declared_report = declared_test_plugins_report(
        deps, include_extras=True, runtime_dependencies=runtime)
    declared = sorted(declared_report["plugins"])

    uses_uv = task_id in UV_ALL_EXTRAS
    try:
        plan = builder_install_plan(
            deps,
            pinned=[PYTEST_PIN],
            extra_deps=EXTRA_DEPS.get(task_id, []),
            include_extras=uses_uv,
            runtime_dependencies=runtime,
            mode="uv" if uses_uv else "pip",
            uv_default_groups=context["uv_default_groups"],
        )
    except AcquisitionError as exc:
        # The structure itself cannot be trusted: UNKNOWN, never "compatible".
        result["reasons"] = ["dependency_structure_untrusted"]
        result["evidence"]["untrusted_detail"] = str(exc)[:400]
        result["declared_plugins"] = declared
        return result

    installed_names = set(plan["packages"])
    installed_plugins = sorted(p for p in declared if p in installed_names)
    missing = sorted(p for p in declared if p not in installed_names)
    problems = plan["problems"]
    result.update(
        declared_plugins=declared,
        installed_plugins=installed_plugins,
        missing_plugins=missing,
    )
    result["evidence"].update({
        "plan_mode": plan["mode"],
        "include_extras": plan["include_extras"],
        "explicit_requirements": plan["explicit_requirements"],
        "requirements_names": list(deps.get("requirements_names") or []),
        "install_mechanisms": {k: v for k, v in plan["installed_packages"].items()
                               if k in set(declared)},
    })

    status = list(context.get("metadata_status") or [])
    if status:
        result["evidence"]["metadata_status"] = status
    if missing:
        # A requirements-sourced declaration is only PROVEN absent from the
        # ENVIRONMENT when the package's dependency metadata is statically
        # complete.  With a missing [project] table or dynamic dependencies,
        # the executed build logic (setup.py) commonly reads requirements.txt
        # into install_requires, so "absent from the static plan" would be an
        # absence of evidence read as evidence of absence: UNKNOWN, never
        # INCOMPATIBLE.  Extra- and group-sourced declarations are unaffected
        # (no contract installs an unselected extra; dynamic `dependencies`
        # never grant extras), so any such mismatch is still proven.
        proven, unverifiable = [], []
        for name in missing:
            by_source = declared_report["plugins"][name].get("by_source") or {}
            requirements_only = bool(by_source.get("requirements")) and not (
                by_source.get("group") or by_source.get("extra")
                or by_source.get("runtime"))
            (unverifiable if status and requirements_only else proven).append(name)
        if unverifiable:
            result["evidence"]["unverifiable_missing"] = unverifiable
        if proven:
            result["classification"] = CLASS_INCOMPATIBLE
        elif unverifiable:
            result["classification"] = CLASS_UNKNOWN
            result["reasons"] = sorted(
                set(status) | {"requirements_plugin_under_delegated_metadata"})
            result["evidence"]["problems"] = problems[:8]
            return result
        for name in proven:
            info = declared_report["plugins"][name]
            sources = info.get("sources") or []
            by_source = info.get("by_source") or {}
            in_extra_only = bool(by_source.get("extra")) and not (
                by_source.get("group") or by_source.get("runtime"))
            in_requirements_only = bool(by_source.get("requirements")) and not (
                by_source.get("group") or by_source.get("runtime"))
            if in_requirements_only:
                note = ("declared only in requirements*.txt, which the build "
                        "contract never installs")
                if by_source.get("extra"):
                    note += (" (and in an optional extra the contract does "
                             "not select)")
            elif in_extra_only:
                note = ("declared only in an optional extra the build contract "
                        "does not select")
            else:
                note = "declared by the project but absent from the install plan"
            result["mismatches"].append({
                "plugin": name,
                "declarations": info.get("declarations"),
                "sources": sources,
                "builder_installs": "no",
                "note": note,
            })
        return result

    if problems:
        # Readable but not verifiable offline (marker, direct reference,
        # unreadable entry): UNKNOWN in the conservative direction.
        result["classification"] = CLASS_UNKNOWN
        result["reasons"] = sorted({p["code"] for p in problems})
        result["evidence"]["problems"] = problems[:8]
        return result
    if status:
        # The dependency set is produced by executed build logic, so "no plugin
        # declared" would be an absence of evidence read as evidence of absence.
        result["classification"] = CLASS_UNKNOWN
        result["reasons"] = sorted(set(status))
        return result

    result["classification"] = CLASS_COMPATIBLE
    return result


def classify_population(*, limit: int | None = None) -> dict[str, Any]:
    """Reproduce the population and classify every selectable task, offline."""
    started = time.time()
    order = load_registration_order(ORDER)
    conflicts = scan_cross_source_conflicts(CORPUS)
    conflicts_in_registration = [t for t in order if t in conflicts]
    recorded_ids, ledger_rows = _ledger_task_ids(LEDGER)
    recorded_set = set(recorded_ids)
    conflict_set = set(conflicts_in_registration)

    # Conflict ids are NOT removed from the candidate population.  The union
    # already resolved each one deterministically -- `SOURCE_PRIORITY` keeps the
    # highest-priority source's record and marks the other
    # `conflicting_cross_source_priority_selected` -- so the operative row is a
    # legitimate registered task.  Removing them here subtracted 185 RESOLVED
    # rows from the pool: a double subtraction against the registration's own
    # accounting, which already counts them exactly once.
    #
    # Preserved: the LEDGER exclusion (a task already recorded is not
    # re-selected) and the overlap report, so an id that is both a resolved
    # conflict and already recorded is excluded from SELECTION exactly once.
    overlap = sorted(conflict_set & recorded_set)
    selectable = [t for t in order if t not in recorded_set]
    if limit is not None:
        selectable = selectable[:int(limit)]

    rows, corpus_digests = _slim_rows(set(selectable))
    results: list[dict[str, Any]] = []
    for task_id in selectable:
        meta = rows.get(task_id)
        if meta is None:
            results.append({
                "task_id": task_id, "classification": CLASS_UNKNOWN,
                "reasons": ["corpus_row_absent"], "declared_plugins": [],
                "installed_plugins": [], "missing_plugins": [],
                "mismatches": [], "evidence": {},
            })
            continue
        results.append(_classify_one(task_id, meta))

    counts = {cls: sum(1 for r in results if r["classification"] == cls)
              for cls in CLASSES}
    by_reason: dict[str, int] = {}
    for r in results:
        if r["classification"] in (CLASS_UNKNOWN, CLASS_NOT_APPLICABLE):
            for reason in r["reasons"] or ["unspecified"]:
                by_reason[reason] = by_reason.get(reason, 0) + 1
    by_plugin: dict[str, int] = {}
    for r in results:
        for mismatch in r["mismatches"]:
            by_plugin[mismatch["plugin"]] = by_plugin.get(mismatch["plugin"], 0) + 1
    by_source: dict[str, dict[str, int]] = {}
    for r in results:
        bucket = by_source.setdefault(str(r.get("source")), {c: 0 for c in CLASSES})
        bucket[r["classification"]] += 1

    total = len(results)
    return {
        "rule_version": PLUGIN_CHECK_RULE_VERSION,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "elapsed_s": round(time.time() - started, 1),
        "population": {
            "registered": len(order),
            "conflicts_total_both_sources": len(conflicts),
            "conflicts_in_registration": len(conflicts_in_registration),
            "conflicts_already_recorded_overlap": len(overlap),
            "conflict_resolution_policy": (
                "SOURCE_PRIORITY: a cross-source conflict's operative row is the "
                "highest-priority source's row; those ids ARE included and classified"
            ),
            "conflicts_included_in_population": len(conflicts_in_registration),
            "ledger_rows": ledger_rows,
            "ledger_distinct_task_ids": len(recorded_ids),
            "selectable": len(selectable),
            "classified": total,
        },
        # Provenance: the exact ids the existing SOURCE_PRIORITY policy resolved
        # and that are therefore classified here (retained, not discarded).
        "resolved_by_source_priority": sorted(conflicts_in_registration),
        "counts": counts,
        "percentages": {cls: (round(100.0 * counts[cls] / total, 3) if total else 0.0)
                        for cls in CLASSES},
        "unknown_reasons": dict(sorted(by_reason.items())),
        "mismatches_by_plugin": dict(sorted(by_plugin.items())),
        "by_source": {k: v for k, v in sorted(by_source.items())},
        "incompatible_tasks": [r for r in results
                               if r["classification"] == CLASS_INCOMPATIBLE],
        "unknown_tasks": [{"task_id": r["task_id"], "reasons": r["reasons"]}
                          for r in results if r["classification"] == CLASS_UNKNOWN],
        "not_applicable_tasks": [r["task_id"] for r in results
                                 if r["classification"] == CLASS_NOT_APPLICABLE],
        "results": results,
        "provenance": {
            "registration_file": str(ORDER),
            "registration_sha256": _sha256_file(ORDER),
            "corpus_sha256": corpus_digests,
            "source_manifest_sha256": _sha256_file(SOURCE_MANIFEST)
            if SOURCE_MANIFEST.is_file() else None,
            "ledger_file": str(LEDGER),
            "ledger_sha256": _sha256_file(LEDGER) if LEDGER.is_file() else None,
            "tree_roots_consulted": [str(p) for p in TREE_ROOTS],
            "builder_constants": {
                "PYTEST_PIN": PYTEST_PIN,
                "EXTRA_DEPS": EXTRA_DEPS,
                "UV_ALL_EXTRAS": UV_ALL_EXTRAS,
            },
        },
        "limitations": [
            "Plugin-install coverage ONLY: this does not say a task builds, its "
            "declared nodes exist, its environment runs, or that it is "
            "scientifically analyzable.",
            "Inclusion in this population is NOT admission, buildability, or "
            "scientific analyzability. The SOURCE_PRIORITY-resolved cross-source "
            "ids are ordinary candidates, classified like any other.",
            "UNKNOWN means the evidence is missing or not verifiable offline; it "
            "is never counted as compatible.",
            "A local clone is not evidence that anything was installed, and a "
            "declared dependency is not evidence that anything was installed -- "
            "only an install-plan mechanism is.",
            "installed_packages lists directly-contracted requirements, not the "
            "transitive closure (resolution would require network access).",
            "No corpus, package index, environment, guest or pilot was contacted; "
            "nothing was installed or executed from any project tree.",
        ],
    }


def _write_report(report: Mapping[str, Any], output_dir: Path = OUTPUT_DIR) -> dict[str, str]:
    """Write the machine-readable report, its digest, and a human summary."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "plugin_compat_report.json"
    json_bytes = json.dumps(report, indent=1, sort_keys=True).encode("utf-8")
    json_path.write_bytes(json_bytes)
    digest = _sha256(json_bytes)
    (output_dir / "plugin_compat_report.sha256").write_text(
        f"{digest}  plugin_compat_report.json\n", encoding="utf-8")

    counts = report["counts"]
    pop = report["population"]
    total = pop["classified"] or 1
    lines = [
        "# F2 S8 offline plugin-compatibility classification",
        "",
        f"- rule version: `{report['rule_version']}`",
        f"- generated: {report['generated_at']} (elapsed {report['elapsed_s']}s)",
        f"- report: `plugin_compat_report.json`  sha256 `{digest}`",
        "",
        "## Population (recomputed from canonical inputs)",
        "",
        "| quantity | count |",
        "|---|---|",
        f"| registered ids | {pop['registered']} |",
        f"| cross-source conflicts (both sources) | {pop['conflicts_total_both_sources']} |",
        f"| ...in the operative registration | {pop['conflicts_in_registration']} |",
        f"| ...resolved by `SOURCE_PRIORITY` and INCLUDED in the population | "
        f"{pop['conflicts_included_in_population']} |",
        f"| ledger rows | {pop['ledger_rows']} |",
        f"| ledger distinct task ids | {pop['ledger_distinct_task_ids']} |",
        f"| ...overlap with conflicts (not subtracted twice) | "
        f"{pop['conflicts_already_recorded_overlap']} |",
        f"| **selectable** | **{pop['selectable']}** |",
        f"| classified | {pop['classified']} |",
        "",
        "## Classification",
        "",
        "| class | count | % |",
        "|---|---|---|",
    ]
    for cls in CLASSES:
        lines.append(f"| {cls} | {counts[cls]} | "
                     f"{round(100.0 * counts[cls] / total, 2)}% |")
    lines += ["", "### Unknown reasons", ""]
    lines += [f"- `{reason}`: {n}" for reason, n in
              report["unknown_reasons"].items()] or ["- none"]
    lines += ["", "### Declared plugins missing from the install plan", ""]
    lines += [f"- `{name}`: {n} task(s)" for name, n in
              report["mismatches_by_plugin"].items()] or ["- none"]
    lines += ["", "### Per source", ""]
    lines.append("| source | " + " | ".join(CLASSES) + " |")
    lines.append("|" + "---|" * (len(CLASSES) + 1))
    for source, bucket in report["by_source"].items():
        lines.append(f"| {source} | " +
                     " | ".join(str(bucket[c]) for c in CLASSES) + " |")
    lines += ["", "## Reproducibility", ""]
    prov = report["provenance"]
    lines += [f"- registration: `{prov['registration_sha256']}`",
              f"- ledger: `{prov['ledger_sha256']}` (read only)"]
    lines += [f"- corpus `{k}`: `{v}`" for k, v in prov["corpus_sha256"].items()]
    lines += ["", "## Limitations", ""]
    lines += [f"- {item}" for item in report["limitations"]]
    lines.append("")
    md_path = output_dir / "plugin_compat_report.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {"json": str(json_path), "json_sha256": digest, "md": str(md_path),
            "md_sha256": _sha256_file(md_path)}


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None,
                        help="classify only the first N selectable tasks (smoke)")
    parser.add_argument("--out", default=str(OUTPUT_DIR))
    args = parser.parse_args(list(argv) if argv is not None else None)
    report = classify_population(limit=args.limit)
    paths = _write_report(report, Path(args.out))
    print(f"selectable={report['population']['selectable']} "
          f"classified={report['population']['classified']}")
    for cls in CLASSES:
        print(f"  {cls:22s} {report['counts'][cls]:5d}  "
              f"({report['percentages'][cls]}%)")
    print(f"report json  : {paths['json']}")
    print(f"report sha256: {paths['json_sha256']}")
    print(f"report md    : {paths['md']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
