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
import json
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping

from qwen_train.f2_population_build import source_to_screen_row
from qwen_train.f2_s8_acquisition import (
    builder_install_plan,
    project_install_context,
    repo_url_for,
)

GIT = r"C:\Program Files\Git\cmd\git.exe"
INPUTS = Path(r"C:\Users\rober\F2_EVIDENCE\s8\pilot_inputs\full")
PATCHES = Path(r"C:\Users\rober\F2_EVIDENCE\s8\pilot_build\patches")
HOST_PYTHON = r"C:\Users\rober\Projects\v-horseshoe-v2\.venv\Scripts\python.exe"
ROOT = Path(r"C:\f2_pilot")

#: Interpreters available on the acquisition host, newest first. A task's
#: environment must satisfy the project's own ``requires-python``; picking one
#: interpreter for every task silently breaks any project whose upper bound
#: excludes it (dspy pins ``>=3.10, <3.14`` and refused 3.14).
INTERPRETERS = (
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
    """
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
PYTEST_PIN = "pytest==8.3.5"

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


def _requires_python(project: Path) -> tuple[int, int] | None:
    """Read ``requires-python`` from the project metadata, if it declares one."""
    import re

    for name in ("pyproject.toml", "setup.cfg"):
        f = project / name
        if not f.is_file():
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        m = re.search(r'requires-python\s*=\s*"([^"]+)"', text)
        if not m:
            m = re.search(r"python_requires\s*=\s*['\"]([^'\"]+)['\"]", text)
        if not m:
            continue
        spec = m.group(1)
        upper = re.search(r"<\s*(\d+)\.(\d+)", spec)
        lower = re.search(r">=\s*(\d+)\.(\d+)", spec)
        lo = (int(lower.group(1)), int(lower.group(2))) if lower else (3, 0)
        hi = (int(upper.group(1)), int(upper.group(2))) if upper else (99, 0)
        return lo, hi  # type: ignore[return-value]
    return None


def pick_interpreter(project: Path) -> str:
    """Choose the first available interpreter the project's metadata accepts.

    Fails loudly rather than building an environment the package manager will
    reject mid-install, which is how a pin mismatch first surfaced for dspy.
    """
    bounds = _requires_python(project)
    for candidate in INTERPRETERS:
        if not Path(candidate).is_file():
            continue
        if bounds is None:
            return candidate
        lo, hi = bounds  # type: ignore[misc]
        ver = _version_of(candidate)
        if lo <= ver < hi:
            return candidate
    raise RuntimeError(
        f"no available interpreter satisfies requires-python={bounds!r} "
        f"from {INTERPRETERS}"
    )


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


def build(task_id: str, url: str, *, inputs: Path = INPUTS,
          patches: Path = PATCHES) -> dict:
    rec = json.loads((Path(inputs) / f"{task_id}.json").read_text(encoding="utf-8"))
    base_commit = rec["base_commit"]
    ws = ROOT / task_id
    repo = ws / "repo"
    gold_repo = ws / "repo_gold"

    _rmtree(repo)
    _rmtree(gold_repo)
    ws.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    run([GIT, "clone", "--filter=blob:none", "--quiet", url, str(repo)])
    run([GIT, "checkout", "--quiet", base_commit], cwd=repo)
    run([GIT, "apply", str(Path(patches) / f"{task_id}.test_patch")], cwd=repo)

    # Python layout differs per repository.
    sub = ""
    if task_id.startswith("HypothesisWorks"):
        sub = "hypothesis-python"
    project = repo / sub if sub else repo
    gold_project = gold_repo / sub if sub else gold_repo

    # Gold tree is cut BEFORE either venv exists, so the copy cannot inherit a
    # base virtualenv (which would waste gigabytes and, worse, shadow the gold
    # environment that is about to be built for it).
    shutil.copytree(repo, gold_repo, symlinks=True,
                    ignore=shutil.ignore_patterns(".venv", ".venvuv", "__pycache__"))
    run([GIT, "apply", str(Path(patches) / f"{task_id}.gold_patch")], cwd=gold_repo)

    base_python = pick_interpreter(project)
    extra = EXTRA_DEPS.get(task_id, [])
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
            _rmtree(venv)
            run([base_python, "-m", "venv", str(venv)])
            vpy = venv / "Scripts" / "python.exe"
            run([str(vpy), "-m", "pip", "install", "--quiet",
                 "--disable-pip-version-check", "--upgrade", "pip"], timeout=1800)
            run([str(vpy), "-m", "pip", "install", "--quiet",
                 "--disable-pip-version-check", PYTEST_PIN, *extra], timeout=1800)
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
            plan = builder_install_plan(
                context["dependency_inputs"],
                pinned=[PYTEST_PIN],
                extra_deps=extra,
                runtime_dependencies=context["runtime_dependencies"],
            )
            install_plan[label] = plan
            if plan["plugin_requirements"]:
                run([str(vpy), "-m", "pip", "install", "--quiet",
                     "--disable-pip-version-check", *plan["plugin_requirements"]],
                    timeout=3600)
            run([str(vpy), "-m", "pip", "install", "--quiet",
                 "--disable-pip-version-check", "-e", str(proj)], timeout=3600)
            envs[label] = str(vpy)

    # Syncing here -- on the acquisition host, with the network -- populates the
    # project venvs so the guest can run the same command with UV_OFFLINE=1 and
    # never reach for the network. uv's wheel cache is NOT required for that:
    # a complete venv plus an intact lockfile resolves with an empty cache.
    if uses_uv:
        for tree in (repo, gold_repo):
            run(["uv", "sync", "--frozen", "--all-extras"], cwd=tree, timeout=3600)

    return {
        "task_id": task_id, "base_commit": base_commit, "workspace": str(ws),
        "repo": str(repo), "gold_repo": str(gold_repo),
        "uses_uv": uses_uv,
        "venv": str(ws / ".venv"), "gold_venv": str(ws / ".venv_gold"),
        "python": envs["base"], "gold_python": envs["gold"],
        "project": str(project), "gold_project": str(gold_project),
        "base_interpreter": base_python if not uses_uv else "",
        "extra_deps": extra,
        # Provenance: exactly which packages this build installed on each side,
        # derived from the same source of truth the offline classifier checks.
        "install_plan": install_plan,
        "requires_python": list(_requires_python(project)) if _requires_python(project) else None,
        "elapsed_s": int(time.time() - t0),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("task_ids", nargs="*")
    ap.add_argument("--manifest", default=str(ROOT / "build_manifest.json"))
    ap.add_argument("--inputs", default=str(INPUTS),
                    help="task record directory (one <task_id>.json per task)")
    ap.add_argument("--patches", default=str(PATCHES),
                    help="directory holding <task_id>.test_patch / .gold_patch")
    args = ap.parse_args(argv)

    ids = args.task_ids or list(REPOS)
    manifest_path = Path(args.manifest)
    inputs = Path(args.inputs)
    patches = Path(args.patches)
    manifest = {}
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    for task_id in ids:
        url = resolve_repo_url(task_id, inputs)
        if not url:
            print(f"SKIP {task_id}: no repository mapping", flush=True)
            continue
        print(f"BUILD {task_id} ...", flush=True)
        try:
            entry = build(task_id, url, inputs=inputs, patches=patches)
        except Exception as exc:  # noqa: BLE001 - recorded, not fatal to the batch
            print(f"  FAILED: {type(exc).__name__}: {exc}", flush=True)
            manifest[task_id] = {"status": "build_failure", "detail": str(exc)[:2000]}
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
