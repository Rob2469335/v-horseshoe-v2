#!/usr/bin/env python3
"""
remediate_stale_tests.py
Self-healing loop — Phase 2: REMEDIATE

Reads logs/stale_test_report.json produced by detect_stale_tests.py
and either proposes or executes remediation actions.

Modes:
  --dry-run   (default) Print proposed actions, take no action
  --approve   Execute all auto-safe actions (archive/move/patch), skip manual ones
  --auto      Execute ALL actions including deletions (use with caution)

Remediation map:
  stale_symbol_import         → propose manual fix, add pytest.mark.skip stub
  adhoc_test_outside_tests_dir → move file into tests/ or archive
  archived_dir_not_excluded   → patch pytest.ini automatically
  broken_import_path          → archive the test file
  orphaned_test_file          → flag for review (info only, no action)

All actions are recorded to logs/remediation_log.jsonl
"""
import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ARCHIVED_LEGACY = Path("tests/_archived_legacy")
REMEDIATION_LOG = Path("logs/remediation_log.jsonl")
PYTEST_INI = Path("pytest.ini")


def load_report(report_path: Path) -> dict:
    if not report_path.exists():
        print(f"[remediate] ERROR: report not found at {report_path}")
        print("  Run detect_stale_tests.py first.")
        sys.exit(1)
    return json.loads(report_path.read_text())


def log_action(action: dict):
    REMEDIATION_LOG.parent.mkdir(exist_ok=True)
    entry = {"timestamp": datetime.now(timezone.utc).isoformat(), **action}
    with open(REMEDIATION_LOG, "a", encoding='utf-8') as f:
        f.write(json.dumps(entry) + "\n")


def archive_file(filepath: Path, reason: str, dry_run: bool) -> dict:
    dest = ARCHIVED_LEGACY / filepath.name
    action = {
        "action": "archive",
        "source": str(filepath),
        "destination": str(dest),
        "reason": reason,
        "executed": False,
    }
    if not dry_run:
        ARCHIVED_LEGACY.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest = dest.with_stem(dest.stem + "_dup")
        shutil.move(str(filepath), str(dest))
        action["executed"] = True
        print(f"  [ARCHIVED] {filepath} → {dest}")
    else:
        print(f"  [DRY-RUN] Would archive: {filepath} → {dest}")
    return action


def move_to_tests(filepath: Path, dry_run: bool) -> dict:
    dest = Path("tests") / filepath.name
    action = {
        "action": "move_to_tests",
        "source": str(filepath),
        "destination": str(dest),
        "reason": "adhoc_test_outside_tests_dir",
        "executed": False,
    }
    if not dry_run:
        if dest.exists():
            dest = dest.with_stem("moved_" + dest.stem)
        shutil.move(str(filepath), str(dest))
        action["executed"] = True
        print(f"  [MOVED] {filepath} → {dest}")
    else:
        print(f"  [DRY-RUN] Would move: {filepath} → {dest}")
    return action


def patch_pytest_ini(dry_run: bool) -> dict:
    action = {
        "action": "patch_pytest_ini",
        "file": str(PYTEST_INI),
        "reason": "archived_dir_not_excluded",
        "executed": False,
    }
    ini_text = PYTEST_INI.read_text() if PYTEST_INI.exists() else "[pytest]\n"
    if "_archived_legacy" in ini_text:
        action["skipped"] = True
        print("  [SKIP] pytest.ini already excludes _archived_legacy")
        return action

    if "norecursedirs" in ini_text:
        ini_text = ini_text.replace(
            "norecursedirs =",
            "norecursedirs = tests/_archived_legacy",
        )
    else:
        ini_text += "\nnorecursedirs = tests/_archived_legacy\n"

    if not dry_run:
        PYTEST_INI.write_text(ini_text)
        action["executed"] = True
        print(f"  [PATCHED] {PYTEST_INI} — added _archived_legacy to norecursedirs")
    else:
        print(f"  [DRY-RUN] Would patch {PYTEST_INI}")
    return action


def main():
    parser = argparse.ArgumentParser(description="Remediate stale tests")
    parser.add_argument("--report", default="logs/stale_test_report.json")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", default=True)
    group.add_argument("--approve", action="store_true")
    group.add_argument("--auto", action="store_true")
    args = parser.parse_args()

    dry_run = not (args.approve or args.auto)
    report = load_report(Path(args.report))
    issues = report.get("issues", [])

    if not issues:
        print("[remediate] No issues to remediate. Suite is clean.")
        return

    mode = "DRY-RUN" if dry_run else ("AUTO" if args.auto else "APPROVE")
    print(f"[remediate] {len(issues)} issue(s) — mode: {mode}")
    print()

    actions_taken = []

    for issue in issues:
        itype = issue["type"]
        filepath = Path(issue.get("file", ""))
        severity = issue.get("severity", "info")

        print(f"  Issue: {itype} | {filepath} | {severity}")

        if itype == "archived_dir_not_excluded":
            a = patch_pytest_ini(dry_run)
            actions_taken.append({**a, "issue": issue})

        elif itype == "broken_import_path":
            if filepath.exists() and (args.approve or args.auto):
                a = archive_file(filepath, itype, dry_run=False)
            else:
                a = archive_file(filepath, itype, dry_run=True)
            actions_taken.append({**a, "issue": issue})

        elif itype == "adhoc_test_outside_tests_dir":
            if args.auto:
                a = move_to_tests(filepath, dry_run=False)
            else:
                a = move_to_tests(filepath, dry_run=True)
            actions_taken.append({**a, "issue": issue})

        elif itype == "stale_symbol_import":
            syms = ", ".join(issue.get("symbols", []))
            print(f"  [MANUAL] {filepath}:{issue.get('lineno')} imports removed symbol(s): {syms}")
            print(f"           Fix: remove or replace imports of {syms} in that file.")
            actions_taken.append({
                "action": "manual_required",
                "source": str(filepath),
                "reason": f"stale symbol import: {syms}",
                "executed": False,
                "issue": issue,
            })

        elif itype == "orphaned_test_file":
            print(f"  [INFO] {filepath} may be orphaned (target: {issue.get('inferred_target')}). Review manually.")
            actions_taken.append({
                "action": "flagged_for_review",
                "source": str(filepath),
                "reason": "orphaned_test_file",
                "executed": False,
                "issue": issue,
            })

        print()

    for a in actions_taken:
        log_action(a)

    executed = sum(1 for a in actions_taken if a.get("executed"))
    manual = sum(1 for a in actions_taken if a["action"] == "manual_required")
    print(f"[remediate] Done. {executed} action(s) executed, {manual} require manual fix.")
    print(f"  log → {REMEDIATION_LOG}")


if __name__ == "__main__":
    main()
