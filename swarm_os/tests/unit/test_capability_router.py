import pytest
from swarm_os.capabilities.capability_router import CapabilityRouter
from swarm_os.capabilities.models import ChatSearchRequest, UpworkAnalysisRequest, VSCodeAutomationRequest

pytestmark = pytest.mark.anyio

@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"

async def test_router_successfully_dispatches_chat_search():
    router = CapabilityRouter()
    req = ChatSearchRequest(query="system setup")
    response = await router.execute("chat_search", req)
    assert response.status == "success"
    assert "Found" in response.message

async def test_router_successfully_dispatches_upwork_analyzer():
    router = CapabilityRouter()
    req = UpworkAnalysisRequest(job_description="Need a python developer for automation scripts.")
    response = await router.execute("upwork_analyzer", req)
    assert response.status == "success"
    assert response.primary_domain == "coding"

async def test_router_successfully_dispatches_vscode_automation():
    router = CapabilityRouter()
    req = VSCodeAutomationRequest(command="list_files")
    response = await router.execute("vscode_automation", req)
    assert response.status == "executed"
    assert "models.py" in response.stdout

def test_router_list_capabilities():
    router = CapabilityRouter()
    caps = router.list_capabilities()
    assert "chat_search" in caps
    assert "upwork_analyzer" in caps
    assert "vscode_automation" in caps
