from __future__ import annotations

from typing import List, Dict, Any, Optional
from .governor_models import SkillRecord
import time


class SkillExtractor:
    """Scan timeline failures and extract repeated successful repair sequences into SkillRecords.

    Simple rule: if the same sequence of repair_attempts (by action names) succeeds N times within
    recent M failures, create or update a SkillRecord.
    """

    def __init__(self, learner=None, min_occurrences: int = 3, lookback: int = 50, store_path: Optional[str] = None):
        self.learner = learner
        self.min_occurrences = min_occurrences
        self.lookback = lookback
        self.store_path = store_path or None
        # skills persisted to a JSON file next to learner if provided
        self._skills: Dict[str, Any] = {}

    def _load_skills(self):
        if self._skills:
            return
        if self.store_path:
            try:
                import json
                with open(self.store_path, 'r', encoding='utf-8') as fh:
                    self._skills = json.load(fh) or {}
            except Exception:
                self._skills = {}
        else:
            self._skills = {}

    def _save_skills(self):
        if not self.store_path:
            return
        try:
            import json
            with open(self.store_path, 'w', encoding='utf-8') as fh:
                json.dump(self._skills, fh, indent=2)
        except Exception:
            pass

    def extract(self) -> List[SkillRecord]:
        failures = self.learner.list_failures() if self.learner else []
        recent = failures[: self.lookback]
        # map sequence signature -> occurrences and example
        seq_map: Dict[str, Dict[str, Any]] = {}
        for f in recent:
            attempts = f.get('repair_attempts', []) or []
            # signature: comma-separated actions
            sig = ','.join([a.get('action') for a in attempts if a.get('action')])
            if not sig:
                continue
            entry = seq_map.setdefault(sig, {'count': 0, 'examples': [], 'total_duration': 0.0})
            entry['count'] += 1
            entry['examples'].append(f)
        created: List[SkillRecord] = []
        for sig, info in seq_map.items():
            if info['count'] >= self.min_occurrences:
                # create or update skill
                skill_name = f"skill-{sig[:40]}"
                repair_sequence = []
                first = info['examples'][0]
                for a in first.get('repair_attempts', []):
                    repair_sequence.append({'action': a.get('action'), 'reason': a.get('reason')})
                sr = SkillRecord(skill_name=skill_name, trigger_conditions=[{'signature': sig}], repair_sequence=repair_sequence, prerequisites=[], success_count=info['count'], failure_count=0, confidence=0.9, average_duration=0.0, last_used_at=time.time())
                self._load_skills()
                self._skills[skill_name] = sr.to_dict()
                created.append(sr)
        self._save_skills()
        return created
