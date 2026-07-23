import time
import logging
from dataclasses import dataclass, asdict, field
from typing import List, Dict, Any, Optional

log = logging.getLogger(__name__)

from swarm_os.core.event_bus import event_bus
from swarm_os.core.patch_manager import PatchManager
from swarm_os.core.ci_engine import CIEngine
from swarm_os.core.scoring_engine import ScoringEngine

@dataclass
class Patch:
    id: str
    reason: str
    diff: str
    status: str = "pending"
    score: float = 0.0
    branch: Optional[str] = None
    created_at: float = field(default_factory=time.time)

class Orchestrator:
    """
    The central coordinator for the CI Swarm.
    ONLY controls flow and state transitions.
    """
    def __init__(self):
        self.patch_manager = PatchManager()
        self.ci_engine = CIEngine()
        self.scoring_engine = ScoringEngine()
        self._traces = []

    def run_cycle(self, patch: Patch, confidence: float = 0.9) -> Dict[str, Any]:
        """
        Executes the full Patch Lifecycle:
        Observe -> Branch -> Apply -> CI -> Score -> Decision -> Commit/Rollback
        """
        original_branch = self.patch_manager.get_current_branch()
        feature_branch = None

        try:
            # 1. Start
            event_bus.emit("PATCH_START", patch.id, {"reason": patch.reason})
            
            # 2. Safety Check
            self.patch_manager.assert_clean_repo()
            
            # 3. Branching
            feature_branch = self.patch_manager.create_isolation_branch(patch.id)
            patch.branch = feature_branch
            event_bus.emit("BRANCH_CREATED", patch.id, {"branch": feature_branch, "base": original_branch})
            
            # 4. Apply
            self.patch_manager.apply_patch_diff(patch.diff)
            event_bus.emit("PATCH_APPLIED", patch.id, {})
            
            # 5. CI
            event_bus.emit("CI_START", patch.id, {})
            ci_results = self.ci_engine.run_suite()
            event_bus.emit("CI_RESULT", patch.id, ci_results)
            
            # 6. Scoring
            patch.score = self.scoring_engine.calculate(ci_results, confidence)
            event_bus.emit("SCORE_COMPUTED", patch.id, {"score": patch.score})
            
            # 7. Decision
            if patch.score >= 0.8:
                self.patch_manager.commit_changes(f"swarm: {patch.reason} (score: {patch.score})")
                patch.status = "accepted"
                event_bus.emit("ACCEPTED", patch.id, {"branch": feature_branch, "score": patch.score})
            else:
                self.patch_manager.rollback(original_branch, feature_branch)
                patch.status = "rolled_back"
                event_bus.emit("ROLLED_BACK", patch.id, {"branch": feature_branch, "score": patch.score})

        except Exception as e:
            # Fatal error handling
            patch.status = "failed"
            event_bus.emit("ERROR", patch.id, {"message": str(e)})
            
            if feature_branch:
                try:
                    self.patch_manager.rollback(original_branch, feature_branch)
                except Exception as rollback_err:
                    log.error(f"CRITICAL: Rollback failed, repo in dirty state! Error: {rollback_err}")
            
            res = asdict(patch)
            self._traces.append(res)
            raise e

        res = asdict(patch)
        self._traces.append(res)
        return res

    def get_recent_traces(self, limit: int = 100) -> List[Dict[str, Any]]:
        return self._traces[-limit:]

# Singleton
orchestrator = Orchestrator()
