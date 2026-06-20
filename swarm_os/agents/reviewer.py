from organism_console.events.skill_events import RepairCompletedEvent, ReviewCompletedEvent, SkillLearnedEvent
from swarm_os.memory.intelligence.skill_memory_engine import SkillMemoryEngine
from swarm_os.genome.genome_registry import GenomeRegistry
from organism_console.skills.skill_extractor import SkillExtractor
from organism_console.events.event_bus import EventBus


class ReviewerAgent:
    def __init__(self, bus: EventBus):
        self.bus = bus
        self.memory = SkillMemoryEngine()
        self.extractor = SkillExtractor()
        self.artifact_store = {}
        self.genomes = GenomeRegistry()  # skill_id -> artifact (runtime only, not persisted)

    def on_repair_completed(self, event: RepairCompletedEvent):
        self.bus.publish(ReviewCompletedEvent(
            repair_id=event.id,
            is_success=event.success,
            confidence=1.0 if event.success else 0.0
        ))

        if event.success:
            artifact = self.extractor.extract(event)
            skill = self.memory.merge_or_add_by_pattern(artifact.pattern)

            self.artifact_store[skill.id] = artifact

            self.bus.publish(SkillLearnedEvent(
                pattern=artifact.pattern,
                action=artifact.patch,
                initial_confidence=skill.confidence,
                source_repair_id=event.id
            ))

            print(f"[reviewer] Skill {skill.id}: confidence {skill.confidence}")

        else:
            best = self.memory.select_best(event.signature)
            if best:
                skill, score = best
                print(f"[reviewer] fallback skill {skill.id} score={score}")
