#!/usr/bin/env python3
"""
detect_stale_tests.py
Self-healing loop — Phase 1: DETECT & DIAGNOSE

Scans the repo for stale/broken test situations and writes a
structured JSON report to logs/stale_test_report.json.

Detects:
  - Stale symbol imports  (removed exports e.g. Planner, StateManager)
  - Ad-hoc test files outside tests/
  - Tests inside tests/_archived_legacy that are NOT excluded by pytest.ini
  - Import path errors (module not found for relative imports)
  - Orphaned test files whose target module no longer exists

Usage (from repo root):
    python scripts/detect_stale_tests.py [--repo-root .]
"""
import argparse
import ast
import importlib.util
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Symbols known to have been removed from the codebase
# Add new ones here as modules are refactored
# ---------------------------------------------------------------------------
REMOVED_SYMBOLS: set[str] = set()

# ---------------------------------------------------------------------------
# Patterns that flag a file as an ad-hoc test
# ---------------------------------------------------------------------------
ADHOC_PATTERN = re.compile(r"(^|_)test.*\.py$", re.IGNORECASE)


def find_py_files(root: Path, exclude_dirs: set[str]) -> list[Path]:
    results = []
    for p in root.rglob("*.py"):
        if any(exc in p.parts for exc in exclude_dirs):
            continue
        results.append(p)
    return results


def parse_imports(filepath: Path) -> list[dict]:
    """Return list of {module, names, lineno} for every import in a file."""
    try:
        tree = ast.parse(filepath.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return []
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append({"module": alias.name, "names": [], "lineno": node.lineno})
        elif isinstance(node, ast.ImportFrom):
            names = [a.name for a in node.names]
            imports.append({
                "module": node.module or "",
                "names": names,
                "lineno": node.lineno,
            })
    return imports


def check_stale_symbols(filepath: Path) -> list[dict]:
    issues = []
    for imp in parse_imports(filepath):
        hit = REMOVED_SYMBOLS & set(imp["names"])
        if hit:
            issues.append({
                "type": "stale_symbol_import",
                "file": str(filepath),
                "symbols": sorted(hit),
                "module": imp["module"],
                "lineno": imp["lineno"],
                "diagnosis": "removed_export",
                "severity": "error",
            })
    return issues


def check_adhoc_outside_tests(repo_root: Path, tests_dir: Path) -> list[dict]:
    issues = []
    for p in repo_root.rglob("*.py"):
        try:
            p.relative_to(tests_dir)
            continue
        except ValueError:
            pass
        rel = p.relative_to(repo_root).as_posix().lower()
        if rel.startswith("tests/") or rel.startswith("swarm_os/tests/"):
            continue
        if ".venv/" in rel or "site-packages/" in rel or "__pycache__/" in rel:
            continue
        if p.name.startswith("test_") or p.name.endswith("_test.py"):
            issues.append({
                "type": "adhoc_test_outside_tests_dir",
                "file": str(p.relative_to(repo_root)),
                "diagnosis": "misplaced_ad_hoc_test",
                "severity": "warning",
            })
    return issues


def check_archived_not_excluded(pytest_ini: Path, archived_dir: Path) -> list[dict]:
    issues = []
    if not archived_dir.exists():
        return issues
    ini_text = pytest_ini.read_text() if pytest_ini.exists() else ""
    if "_archived_legacy" not in ini_text and "archived_legacy" not in ini_text:
        issues.append({
            "type": "archived_dir_not_excluded",
            "file": str(archived_dir),
            "diagnosis": "missing_norecursedirs_entry",
            "severity": "error",
        })
    return issues


def check_broken_imports(filepath: Path, repo_root: Path) -> list[dict]:
    issues = []
    for imp in parse_imports(filepath):
        mod = imp["module"]
        if not mod or mod.startswith("_") or mod in sys.stdlib_module_names:
            continue
        if not mod.startswith("swarm_os"):
            continue
        spec = importlib.util.find_spec(mod)
        if spec is None:
            issues.append({
                "type": "broken_import_path",
                "file": str(filepath.relative_to(repo_root)),
                "module": mod,
                "lineno": imp["lineno"],
                "diagnosis": "archived_or_deleted_module",
                "severity": "error",
            })
    return issues


def check_orphaned_tests(tests_dir: Path, repo_root: Path) -> list[dict]:
    """Identify test files whose corresponding source module no longer exists."""
    issues = []
    for test_file in tests_dir.glob("test_*.py"):
        stem = test_file.stem[len("test_"):]
        candidates = (
            list((repo_root / "swarm_os").rglob(f"{stem}.py"))
            + list((repo_root / "swarm_os").rglob(f"*{stem}*.py"))
            + list((repo_root / "runtime_v2").rglob(f"{stem}.py"))
            + list((repo_root / "runtime_v2").rglob(f"*{stem}*.py"))
            + list((repo_root / "organism_console").rglob(f"{stem}.py"))
        )
        if not candidates:
            issues.append({
                "file": str(test_file),
                "stem": stem,
                "diagnosis": "orphaned_test_no_source_module",
                "severity": "warning",
            })
    return issues


def main():
    parser = argparse.ArgumentParser(description="Detect stale tests")
    parser.add_argument("--repo-root", default=".", help="Path to repo root")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    tests_dir = repo_root / "tests"
    archived_dir = tests_dir / "_archived_legacy"
    pytest_ini = repo_root / "pytest.ini"
    logs_dir = repo_root / "logs"
    logs_dir.mkdir(exist_ok=True)

    all_issues: list[dict] = []

    for test_file in tests_dir.rglob("test_*.py"):
        if "_archived_legacy" in test_file.parts:
            continue
        all_issues.extend(check_stale_symbols(test_file))
        all_issues.extend(check_broken_imports(test_file, repo_root))

    for py_file in (repo_root / "swarm_os").rglob("*.py"):
        all_issues.extend(check_stale_symbols(py_file))

    all_issues.extend(check_adhoc_outside_tests(repo_root, tests_dir))
    all_issues.extend(check_archived_not_excluded(pytest_ini, archived_dir))
    all_issues.extend(check_orphaned_tests(tests_dir, repo_root))

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(repo_root),
        "total_issues": len(all_issues),
        "summary": {
            "error": sum(1 for i in all_issues if i.get("severity") == "error"),
            "warning": sum(1 for i in all_issues if i.get("severity") == "warning"),
            "info": sum(1 for i in all_issues if i.get("severity") == "info"),
        },
        "issues": all_issues,
    }

    report_path = logs_dir / "stale_test_report.json"
    report_path.write_text(json.dumps(report, indent=2))

    print(f"[detect] Scan complete. {len(all_issues)} issue(s) found.")
    print(f"  errors:   {report['summary']['error']}")
    print(f"  warnings: {report['summary']['warning']}")
    print(f"  info:     {report['summary']['info']}")
    print(f"  report → {report_path}")

    if report["summary"]["error"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()


