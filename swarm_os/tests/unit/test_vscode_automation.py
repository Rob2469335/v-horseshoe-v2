import pytest
from swarm_os.capabilities.vscode_automation import VSCodeAutomationHandler
from swarm_os.capabilities.models import VSCodeAutomationRequest

pytestmark = pytest.mark.anyio

@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"

async def test_vscode_automation_list_files():
    handler = VSCodeAutomationHandler()
    req = VSCodeAutomationRequest(command="list_files", args=[])
    response = await handler.execute(req)

    assert response.status == "executed"
    assert response.command == "list_files"
    assert response.exit_code == 0
    assert "capabilities/models.py" in response.stdout

async def test_vscode_automation_rejects_disallowed_command():
    handler = VSCodeAutomationHandler()
    req = VSCodeAutomationRequest(command="rm_rf", args=["."])
    response = await handler.execute(req)

    assert response.status == "rejected"
    assert response.exit_code == 1
    assert "Security Error" in response.stderr
