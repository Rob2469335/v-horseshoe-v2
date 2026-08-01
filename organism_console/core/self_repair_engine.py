import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List
from collections import Counter

from organism_console.core.repair_engine import (
    classify_failure, TieredRepairOrchestrator, load_cures, save_cures,
    load_lessons, append_lesson, validate_file, meta_classify_lessons,
    run_adversarial_check, KNOWLEDGE_BASE_DIR, load_budget, save_budget,
)

import logging
log = logging.getLogger("zenith_cli")

TESTS_DIR = KNOWLEDGE_BASE_DIR / "generated_tests"


class SelfRepairEngine:
    def __init__(self, cmd_ctx=None):
        self.cmd_ctx = cmd_ctx
        self.repair_orchestrator = TieredRepairOrchestrator(cmd_ctx)
        self.repair_log: List[Dict] = []
        self._stats = {"t0_hits": 0, "t1_hits": 0, "t2_hits": 0, "failures": 0, "tokens_spent": 0}

    def diagnose_and_repair(self, error_text: str, file_path: Optional[Path] = None, context: Optional[Dict] = None) -> Dict[str, Any]:
        result = self.repair_orchestrator.repair(error_text, file_path, context)

        validation_error = result.get("validation_error")
        if validation_error:
            result["fixed"] = False

        tier = result.get("tier_used", 2)
        if result.get("fixed"):
            self._stats[f"t{tier}_hits"] += 1
            test_path = self._save_generated_test(result, error_text)
            if test_path:
                result["generated_test_file"] = str(test_path)
        else:
            self._stats["failures"] += 1
        self._stats["tokens_spent"] += result.get("tokens_used", 0)
        self.repair_log.append(result)

        if self.cmd_ctx:
            self._report(result)

        self._maybe_distill_lesson(result)
        if not result.get("fixed"):
            self._maybe_retire_cure(result)
        return result

    def _report(self, result: Dict[str, Any]):
        console = self.cmd_ctx.console
        tier = result.get("tier_used", "?")
        status = "[green]FIXED[/green]" if result.get("fixed") else "[red]UNRESOLVED[/red]"
        ftype = result.get("failure_type", "unknown")
        val = " [yellow](reverted)[/yellow]" if result.get("validation_error") else ""
        test = " [dim]+test[/dim]" if result.get("generated_test") else ""
        console.print(f"  [{ftype}] T{tier} {status}{val}{test}: {result.get('repair_action', 'no action')[:120]}")

    def _save_generated_test(self, result: Dict[str, Any], error_text: str) -> Optional[Path]:
        test_code = result.get("generated_test")
        if not test_code:
            return None
        TESTS_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r'[^a-zA-Z0-9]', '_', error_text[:40])
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        test_path = TESTS_DIR / f"test_{safe_name}_{timestamp}.py"
        test_path.write_text(test_code, encoding="utf-8")
        return test_path

    def _maybe_distill_lesson(self, result: Dict[str, Any]):
        if not result.get("fixed"):
            return
        failure_type = result.get("failure_type", "unknown")
        action = result.get("repair_action", "")
        if not action:
            return

        cures = load_cures()
        if failure_type not in cures:
            cures[failure_type] = []

        existing = [c for c in cures[failure_type] if c.get("action") == action]
        if existing:
            existing[0]["count"] = existing[0].get("count", 1) + 1
            existing[0]["confidence"] = min(1.0, existing[0]["confidence"] + 0.05)
        else:
            keywords = [w for w in result.get("error", "").lower().split() if len(w) > 4][:10]
            cure_entry = {
                "action": action,
                "keywords": keywords,
                "confidence": result.get("confidence", 0.5),
                "count": 1,
                "success_count": 1,
                "failure_count": 0,
                "distilled_at": datetime.now(timezone.utc).isoformat(),
            }
            if result.get("generated_test"):
                cure_entry["test_file"] = result.get("generated_test_file")
            cures[failure_type].append(cure_entry)

        cures[failure_type] = sorted(cures[failure_type], key=lambda x: -x.get("count", 0))[:50]
        save_cures(cures)

    def _maybe_retire_cure(self, result: Dict[str, Any]):
        """Reflexion-catalog rule: lessons are retired, not silently kept forever.
        When a matching cure fails, decay its confidence; below threshold it is
        removed so stale knowledge stops poisoning future retrievals."""
        error_text = str(result.get("error", "")).lower()
        failure_type = result.get("failure_type", "unknown")
        cures = load_cures()
        entries = cures.get(failure_type)
        if not entries:
            return
        survivors = []
        retired = []
        for cure in entries:
            keywords = cure.get("keywords", [])
            if keywords and any(kw in error_text for kw in keywords):
                cure["failure_count"] = cure.get("failure_count", 0) + 1
                total = cure.get("success_count", 1) + cure["failure_count"]
                cure["confidence"] = cure.get("success_count", 1) / max(total, 1)
                if cure["confidence"] < 0.25:
                    retired.append(str(cure.get("action", ""))[:80])
                    continue
            survivors.append(cure)
        if not retired:
            return
        cures[failure_type] = survivors
        save_cures(cures)
        log.info("Retired %d low-confidence cure(s) for %s: %s", len(retired), failure_type, retired)

    def record_feedback(self, action_text: str, success: bool):
        cures = load_cures()
        updated = False
        for ftype, entries in cures.items():
            survivors = []
            for cure in entries:
                if cure.get("action") == action_text:
                    cure["success_count"] = cure.get("success_count", 1) + (1 if success else 0)
                    cure["failure_count"] = cure.get("failure_count", 0) + (0 if success else 1)
                    total = cure["success_count"] + cure["failure_count"]
                    cure["confidence"] = cure["success_count"] / max(total, 1)
                    updated = True
                if cure.get("confidence", 0.5) >= 0.25:
                    survivors.append(cure)
            cures[ftype] = survivors
        if updated:
            save_cures(cures)
            return True
        return False

    def get_cure_by_action(self, action_text: str) -> Optional[Dict]:
        cures = load_cures()
        for ftype, entries in cures.items():
            for cure in entries:
                if cure.get("action") == action_text:
                    return {**cure, "failure_type": ftype}
        return None

    def show_stats(self) -> Dict[str, Any]:
        return {**self._stats, "total_repairs": len(self.repair_log)}

    def show_lessons(self, failure_type: Optional[str] = None) -> List[Dict]:
        lessons = load_lessons()
        if failure_type:
            lessons = [l for l in lessons if l.get("failure_type") == failure_type]
        return lessons[-20:]

    def show_cures(self) -> Dict[str, List[Dict]]:
        return load_cures()

    def meta_analyze(self) -> Dict[str, Any]:
        lessons = load_lessons()
        return meta_classify_lessons(lessons)

    def run_adversarial(self) -> Dict[str, Any]:
        results = run_adversarial_check(self)
        if self.cmd_ctx:
            self.cmd_ctx.console.print(f"[bold]Adversarial results:[/bold] {results['detected']}/{results['total']} detected, {results['fixed']} fixed")
        return results