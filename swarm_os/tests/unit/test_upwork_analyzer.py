import pytest
from swarm_os.capabilities.upwork_analyzer import UpworkAnalyzerHandler
from swarm_os.capabilities.models import UpworkAnalysisRequest

pytestmark = pytest.mark.anyio

@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"

async def test_upwork_analyzer_detects_coding_job():
    handler = UpworkAnalyzerHandler()
    req = UpworkAnalysisRequest(
        job_description="Need a Python developer for backend API automation and pytest work."
    )
    response = await handler.analyze_job(req)

    assert response.status == "success"
    assert response.primary_domain == "coding"
    assert response.match_score > 0.0
    assert response.should_bid is True
    assert response.recommended_bid is not None
    assert "/hr" in response.recommended_bid.projected_rate

async def test_upwork_analyzer_handles_empty_description():
    handler = UpworkAnalyzerHandler()
    req = UpworkAnalysisRequest(job_description="")
    response = await handler.analyze_job(req)

    assert response.status == "success"
    assert response.primary_domain == "general"
    assert response.match_score == 0.0
    assert response.should_bid is False
    assert response.recommended_bid is None
