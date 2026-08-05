from organism_console.events.skill_events import RepairCompletedEvent
from organism_console.skills.repair_artifact import RepairArtifact
import uuid

class SkillExtractor:
    def extract(self, event: RepairCompletedEvent):
        tool = event.tool
        signature = event.signature
        input_text = event.input
        output_text = event.output
        
        pattern = f"repair:{tool}:{signature}"
        
        # Generate action based on pattern (this is the ONLY place with logic)
        action = self._generate_action(signature, output_text)
        diagnosis = self._diagnose(signature)
        strategy = self._strategy(signature)
        
        skill_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, pattern))
        
        return RepairArtifact(
            skill_id=skill_id,
            pattern=pattern,
            diagnosis=diagnosis,
            patch=action,
            strategy=strategy,
            examples=[input_text]
        )

    def _generate_action(self, signature: str, output: str) -> str:
        """Generate action - ONLY called AFTER successful execution"""
        return f"# Fix for: {signature}\n# {output}"

    def _diagnose(self, signature: str) -> str:
        return f"Issue: {signature}"

    def _strategy(self, signature: str) -> str:
        return "generic_fix"
