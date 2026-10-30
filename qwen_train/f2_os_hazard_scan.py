"""Static scan for Windows-only test-body hazards.

Why this exists
---------------
The registered S8 eligibility predicate (`F2_S8_EXECUTION_AND_PILOT_PACKAGE.md`
§8) is **outcome-independent and syntax-only**: it inspects a task's declared
``test_cmd`` and its declared node ids. It never looks at the test *bodies*.
`static_os_plausibility` in ``f2_task_plan.py`` is explicit that it is "a
hypothesis about syntax, not a result".

That leaves a gap the pilot already fell into once. `stanfordnlp__dspy-8739`
declares a plain ``pytest`` command, so it passes every registered screen, yet
its suite calls ``os.kill(os.getpid(), signal.SIGINT)``. CPython documents that
on Windows any signal other than ``CTRL_C_EVENT``/``CTRL_BREAK_EVENT`` "will
cause the process to be unconditionally killed by the **TerminateProcess** API,
and the exit code will be set to sig". The interpreter dies mid-run, pytest
never finalises, and **no JUnit is written** -- so the task cannot be evaluated
at all on this platform, and its declared nodes read ``missing``.

This module measures how wide that gap is. It is a **diagnostic**, not a
filter: it never changes eligibility, never excludes a task, and never runs
candidate code. It reads source text with ``ast`` and reports what it finds, so
an operator can size the risk and decide whether a platform-compatibility screen
is worth authorising.

Two hazard classes matter, because both prevent an evaluation from completing
rather than merely making one test fail:

``terminates_worker``
    A test signals its own process. The worker dies, so the suite produces no
    JUnit at all. Highest severity: this is what made dspy unevaluatable.

``aborts_collection``
    A test module imports a POSIX-only module at module scope. Import fails
    during collection, and a collection error aborts the whole pytest session
    before a single declared node runs.

Everything else is ``suspect``: a POSIX-only construct that may or may not
fail, worth eyeballing but not evidence of an unevaluatable suite.

What this does NOT establish
----------------------------
A clean scan does not prove a suite runs on Windows. Dependencies, native
extensions, platform-specific fixtures and runtime behaviour are all invisible
to static analysis. A clean scan means only "no *known static* hazard found".
"""
from __future__ import annotations

import argparse
import ast
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

__all__ = [
    "HAZARD_SEVERITIES",
    "POSIX_ONLY_MODULES",
    "Hazard",
    "TaskScan",
    "scan_source",
    "scan_tree",
    "main",
]

#: Ordered least to most severe. Every finding carries exactly one.
HAZARD_SEVERITIES = ("suspect", "aborts_collection", "terminates_worker")

#: Modules that exist only on POSIX. Importing one at module scope in a test
#: file fails that file's import, which pytest reports as a collection error --
#: and a collection error aborts the entire session, so no declared node runs.
POSIX_ONLY_MODULES = frozenset({
    "fcntl", "grp", "pwd", "resource", "termios", "tty", "pty", "nis",
    "spwd", "crypt", "ossaudiodev", "posix",
})

#: Signals that CPython on Windows delivers as TerminateProcess rather than as
#: a signal. ``CTRL_C_EVENT``/``CTRL_BREAK_EVENT`` are the two exceptions.
_TERMINATING_SIGNALS = frozenset({"SIGINT", "SIGTERM", "SIGHUP", "SIGQUIT",
                                  "SIGUSR1", "SIGUSR2", "SIGALRM", "SIGPIPE",
                                  "SIGKILL", "SIGSTOP", "SIGCONT", "SIGWINCH",
                                  "SIGCHLD", "SIGPIPE"})


@dataclass(frozen=True)
class Hazard:
    """One statically observed hazard in one file."""

    severity: str
    kind: str
    line: int
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaskScan:
    """Scan result for one task workspace."""

    task_id: str
    root: str
    files_scanned: int = 0
    hazards: list[Hazard] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def terminates_worker(self) -> list[Hazard]:
        return [h for h in self.hazards if h.severity == "terminates_worker"]

    @property
    def aborts_collection(self) -> list[Hazard]:
        return [h for h in self.hazards if h.severity == "aborts_collection"]

    @property
    def suspect(self) -> list[Hazard]:
        return [h for h in self.hazards if h.severity == "suspect"]

    def worst(self) -> str:
        if self.terminates_worker:
            return "terminates_worker"
        if self.aborts_collection:
            return "aborts_collection"
        if self.suspect:
            return "suspect"
        return "none_found"

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "root": self.root,
            "files_scanned": self.files_scanned,
            "worst": self.worst(),
            "terminates_worker": [h.to_dict() for h in self.terminates_worker],
            "aborts_collection": [h.to_dict() for h in self.aborts_collection],
            "suspect": [h.to_dict() for h in self.suspect],
            "errors": list(self.errors),
        }


class _Visitor(ast.NodeVisitor):
    """Collects hazards from one parsed module.

    Import hazards are decided at ``Import``/``ImportFrom`` nodes so that only
    module-scope imports count: a POSIX import inside a function body fails
    that one test, whereas the same import at module scope fails the file and
    aborts the session.
    """

    def __init__(self) -> None:
        self.hazards: list[Hazard] = []
        self._depth = 0

    # -- imports -----------------------------------------------------------
    def _note_import(self, module: str, node: ast.AST, what: str) -> None:
        top = (module or "").split(".")[0]
        if top in POSIX_ONLY_MODULES and self._depth == 0:
            self.hazards.append(Hazard(
                severity="aborts_collection",
                kind="posix_module_import",
                line=getattr(node, "lineno", 0),
                detail=f"{what} of POSIX-only module {top!r} at module scope",
            ))
        elif top in POSIX_ONLY_MODULES:
            self.hazards.append(Hazard(
                severity="suspect",
                kind="posix_module_import_local",
                line=getattr(node, "lineno", 0),
                detail=f"{what} of POSIX-only module {top!r} inside a function",
            ))

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._note_import(alias.name, node, "import")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level == 0:
            self._note_import(node.module or "", node, "from-import")
        self.generic_visit(node)

    # -- depth, so nested scopes are not treated as module scope ------------
    def _enter(self, node: ast.AST) -> None:
        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1

    visit_FunctionDef = _enter
    visit_AsyncFunctionDef = _enter
    visit_ClassDef = _enter

    # -- self-signalling ----------------------------------------------------
    def _check_kill(self, func: ast.AST, args: Sequence[ast.expr], lineno: int) -> None:
        name = ""
        if isinstance(func, ast.Attribute):
            name = func.attr
        elif isinstance(func, ast.Name):
            name = func.id
        if name not in ("kill", "killpg", "pthread_kill"):
            return
        # The signal argument is the second one for os.kill/os.killpg and the
        # second for signal.pthread_kill.
        if len(args) < 2:
            return
        sig = args[1]
        sig_name = ""
        if isinstance(sig, ast.Attribute):
            sig_name = sig.attr
        elif isinstance(sig, ast.Name):
            sig_name = sig.id

        # os.kill(os.getpid(), sig) / os.killpg(os.getpgrp(), sig) and the
        # signal.<SIG> form are the self-termination shapes.
        self_pid = self._is_own_pid(args[0])
        if name == "kill" and self_pid:
            self.hazards.append(Hazard(
                severity="terminates_worker",
                kind="self_signal_termination",
                line=lineno,
                detail=(
                    f"os.kill(os.getpid(), {sig_name or '<expr>'}): on Windows "
                    "any signal other than CTRL_C_EVENT/CTRL_BREAK_EVENT "
                    "terminates the process via TerminateProcess with exit "
                    "code = sig"
                ),
            ))
        elif name == "killpg" and self_pid:
            self.hazards.append(Hazard(
                severity="terminates_worker",
                kind="self_signal_termination",
                line=lineno,
                detail="os.killpg on own process group terminates this worker",
            ))
        elif name == "pthread_kill":
            self.hazards.append(Hazard(
                severity="suspect",
                kind="posix_signal_api",
                line=lineno,
                detail="signal.pthread_kill is POSIX-only",
            ))

    @staticmethod
    def _is_own_pid(node: ast.expr) -> bool:
        """``os.getpid()`` or ``os.getppid()`` -- i.e. this process."""
        if not isinstance(node, ast.Call):
            return False
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in ("getpid", "getppid"):
            return True
        if isinstance(func, ast.Name) and func.id in ("getpid", "getppid"):
            return True
        return False

    def visit_Call(self, node: ast.Call) -> None:
        self._check_kill(node.func, node.args, node.lineno)
        self.generic_visit(node)

    # -- POSIX-only process APIs -------------------------------------------
    def visit_Attribute(self, node: ast.Attribute) -> None:
        # Only APIs that genuinely do not exist, or behave differently, on
        # Windows. ``os.symlink``/``os.readlink`` are available on Windows from
        # 3.8 and are deliberately excluded rather than reported as noise.
        if node.attr in ("fork", "forkpty", "waitpid", "setsid", "setpgrp",
                         "tcsetpgrp", "WIFSIGNALED", "WTERMSIG", "WIFEXITED",
                         "killpg", "getpgid", "setuid", "setgid", "chroot",
                         "mkfifo", "WNOHANG", "WIFCONTINUED"):
            self.hazards.append(Hazard(
                severity="suspect",
                kind="posix_process_api",
                line=node.lineno,
                detail=f"os.{node.attr} is POSIX-only or POSIX-shaped",
            ))
        self.generic_visit(node)


def scan_source(text: str) -> list[Hazard]:
    """Scan one Python source string. Returns hazards, never raises."""
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return [Hazard(
            severity="suspect", kind="unparseable", line=exc.lineno or 0,
            detail=f"source did not parse: {exc.msg}",
        )]
    visitor = _Visitor()
    visitor.visit(tree)
    return visitor.hazards


def _iter_test_files(root: Path) -> Iterable[Path]:
    """Test-shaped files under a workspace, skipping vendored and VCS trees."""
    skip = {".git", ".venv", "node_modules", "__pycache__", "build", "dist",
            ".tox", ".nox", "site-packages", ".eggs"}
    for path in root.rglob("*.py"):
        if any(part in skip for part in path.parts):
            continue
        name = path.name
        if name.startswith("test_") or name.endswith("_test.py") or "tests" in path.parts:
            yield path


def scan_tree(root: Path | str, *, task_id: str = "") -> TaskScan:
    """Scan every test-shaped file under ``root``. Read-only; never executes."""
    root = Path(root)
    result = TaskScan(task_id=task_id or root.name, root=str(root))
    if not root.is_dir():
        result.errors.append(f"not a directory: {root}")
        return result
    for path in _iter_test_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            result.errors.append(f"{path}: {exc}")
            continue
        result.files_scanned += 1
        rel = path.relative_to(root).as_posix()
        for hazard in scan_source(text):
            result.hazards.append(Hazard(
                severity=hazard.severity, kind=hazard.kind, line=hazard.line,
                detail=f"{rel}:{hazard.line}: {hazard.detail}",
            ))
    return result


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Static Windows-hazard scan for task workspaces (read-only)")
    ap.add_argument("roots", nargs="+", help="workspace directories to scan")
    ap.add_argument("--out", default="", help="write the JSON report here")
    ap.add_argument("--task-id", default="", help="label when scanning a single root")
    args = ap.parse_args(argv)

    scans = [scan_tree(r, task_id=args.task_id or Path(r).name) for r in args.roots]
    report = {
        "scans": [s.to_dict() for s in scans],
        "summary": {
            "tasks": len(scans),
            "terminates_worker": [s.task_id for s in scans if s.terminates_worker],
            "aborts_collection": [s.task_id for s in scans if s.aborts_collection],
            "suspect_only": [s.task_id for s in scans
                             if s.suspect and not s.terminates_worker
                             and not s.aborts_collection],
            "none_found": [s.task_id for s in scans if s.worst() == "none_found"],
        },
    }
    text = json.dumps(report, indent=1, sort_keys=True)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
