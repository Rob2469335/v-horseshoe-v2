#!/usr/bin/env python3
"""
record_healing_event.py
Self-healing loop — Phase 3: RECORD & LEARN

Reads logs/remediation_log.jsonl and produces two outputs:
  1. logs/healing_history.json  — full structured history
  2. logs/policy_hints.json     — pattern-derived policy suggestions

Also supports appending a single manual event via CLI flags.

Usage:
    # Build/refresh history from remediation log
    python scripts/record_healing_event.py --refresh

    # Append a manual event
    python scripts/record_healing_event.py \\
        --action archive \\
        --file tests/test_foo.py \\
        --reason "stale_symbol_import: Planner" \\
        --outcome success
"""
import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REMEDIATION_LOG = Path("logs/remediation_log.jsonl")
HEALING_HISTORY = Path("logs/healing_history.json")
POLICY_HINTS    = Path("logs/policy_hints.json")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    lines = [l.strip() for l in path.read_text().splitlines() if l.strip()]
    return [json.loads(l) for l in lines]


def write_json(path: Path, data):
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def derive_policies(events: list[dict]) -> list[dict]:
    """
    Simple frequency-based policy hints.
    If a pattern appears >= THRESHOLD times → suggest a standing policy.
    """
    THRESHOLD = 2
    reason_counts: Counter = Counter()
    action_by_reason: dict[str, Counter] = {}

    for e in events:
        reason = e.get("reason", "unknown")
        action = e.get("action", "unknown")
        reason_counts[reason] += 1
        action_by_reason.setdefault(reason, Counter())[action] += 1

    hints = []
    for reason, count in reason_counts.items():
        if count >= THRESHOLD:
            most_common_action = action_by_reason[reason].most_common(1)[0][0]
            hints.append({
                "pattern": reason,
                "occurrences": count,
                "suggested_action": most_common_action,
                "confidence": "high" if count >= 5 else "medium",
                "description": (
                    f"Pattern '{reason}' has occurred {count} time(s). "
                    f"Suggested standing action: '{most_common_action}'."
                ),
            })
    return sorted(hints, key=lambda h: h["occurrences"], reverse=True)


def refresh():
    events = read_jsonl(REMEDIATION_LOG)
    history = {
        "refreshed_at": datetime.now(timezone.utc).isoformat(),
        "total_events": len(events),
        "events": events,
    }
    write_json(HEALING_HISTORY, history)
    print(f"[record] history refreshed — {len(events)} event(s) → {HEALING_HISTORY}")

    policies = derive_policies(events)
    hints = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy_hints": policies,
    }
    write_json(POLICY_HINTS, hints)
    print(f"[record] {len(policies)} policy hint(s) → {POLICY_HINTS}")

    if policies:
        print()
        print("[record] Policy suggestions:")
        for p in policies:
            print(f"  [{p['confidence'].upper()}] {p['description']}")


def append_event(action: str, file: str, reason: str, outcome: str):
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "source": file,
        "reason": reason,
        "outcome": outcome,
        "executed": outcome == "success",
    }
    REMEDIATION_LOG.parent.mkdir(exist_ok=True)
    with open(REMEDIATION_LOG, "a", encoding='utf-8') as f:
        f.write(json.dumps(entry) + "\n")
    print(f"[record] event appended → {REMEDIATION_LOG}")
    refresh()


def main():
    parser = argparse.ArgumentParser(description="Record healing events and derive policies")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--action")
    parser.add_argument("--file")
    parser.add_argument("--reason")
    parser.add_argument("--outcome", default="success")
    args = parser.parse_args()

    if args.action and args.file and args.reason:
        append_event(args.action, args.file, args.reason, args.outcome)
    elif args.refresh or not (args.action or args.file):
        refresh()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
