from swarm_os.adaptation.chat_model_adapter import ChatModelAdapter
from swarm_os.adaptation.repair.repair_executor import RepairExecutor
from swarm_os.adaptation.verification.repair_verifier import RepairVerifier
from swarm_os.adaptation.health.health_probe import HealthProbe
from swarm_os.adaptation.healing.healing_engine import HealingEngine


def broken_primary(prompt: str) -> str:
    raise RuntimeError("provider failure")


def healthy_fallback(prompt: str) -> str:
    return f"ok:{prompt}"


adapter = ChatModelAdapter(primary_provider='primary', providers={'primary': broken_primary, 'fallback': healthy_fallback})

executor = RepairExecutor(action_map={
    ('chat_model', 'retry_request'): lambda c, a: (False, 'retry failed'),
    ('chat_model', 'rotate_model_provider'): lambda c, a: (True, f"rotated_to={adapter.rotate_provider()}")
})

verifier = RepairVerifier(probe=HealthProbe(), chat_adapter=adapter)
engine = HealingEngine(state_path=None, executor=executor, verifier=verifier)

print('First execute:')
print(engine.execute({'component': 'chat_model', 'status': 'failed'}))
print('Second execute:')
print(engine.execute({'component': 'chat_model', 'status': 'failed'}))
