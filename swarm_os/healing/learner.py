from __future__ import annotations

import json
import os
from typing import Optional, Dict, Any
from .governor_models import FailureRecord, ReflectionRecord, gen_id


class Learner:
    """Persists FailureRecord and ReflectionRecord. Uses existing LearningService if provided,
    otherwise writes to a timeline JSON file.
    """

    def __init__(self, learning_service: Optional[object] = None, timeline_path: Optional[str] = None):
        self.learning = learning_service
        if timeline_path:
            self.timeline_path = timeline_path
        else:
            # default timeline store next to repo in .data/timeline.json
            base = os.path.dirname(os.path.dirname(__file__))
            data_dir = os.path.join(base, "..", "_data")
            data_dir = os.path.normpath(data_dir)
            os.makedirs(data_dir, exist_ok=True)
            self.timeline_path = os.path.join(data_dir, "timeline.json")

        # load cache
        self._cache = None
        try:
            if os.path.exists(self.timeline_path):
                with open(self.timeline_path, 'r', encoding='utf-8') as fh:
                    self._cache = json.load(fh) or {}
        except Exception:
            self._cache = {}

    def _save_cache(self):
        try:
            with open(self.timeline_path, 'w', encoding='utf-8') as fh:
                json.dump(self._cache, fh, indent=2)
        except Exception:
            pass

    def persist_failure(self, failure: FailureRecord) -> str:
        """Persist a FailureRecord. Returns incident_id."""
        if self.learning:
            # learning service has ingest_outcome/record_repair but not structured failure record; store simple outcome
            try:
                # best effort: call record_repair for successful_fix if available
                if failure.successful_fix:
                    comp = failure.service or failure.symptom.get('component')
                    self.learning.record_repair(comp, failure.successful_fix.get('action', 'unknown'), failure.successful_fix.get('result', ''), failure.successful_fix.get('reason', ''))
            except Exception:
                pass

        # persist to timeline JSON
        if self._cache is None:
            self._cache = {"failures": [], "reflections": []}
        entry = failure.to_dict()
        self._cache.setdefault("failures", []).insert(0, entry)
        # keep a bounded history
        self._cache["failures"] = self._cache.get("failures", [])[:200]
        self._save_cache()
        return entry.get("incident_id")

    def persist_reflection(self, reflection: ReflectionRecord) -> str:
        if self._cache is None:
            self._cache = {"failures": [], "reflections": []}
        entry = reflection.to_dict()
        self._cache.setdefault("reflections", []).insert(0, entry)
        self._cache["reflections"] = self._cache.get("reflections", [])[:200]
        self._save_cache()
        return entry.get("incident_id")

    def list_failures(self):
        if self._cache is None:
            return []
        return list(self._cache.get("failures", []))

    def list_reflections(self):
        if self._cache is None:
            return []
        return list(self._cache.get("reflections", []))
