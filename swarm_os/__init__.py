"""
swarm_os - Core module for the Horseshoe Swarm autonomous agent platform.
"""

from swarm_os.capabilities import (
    ChatSearchRequest,
    ChatSearchResponse,
    UpworkAnalysisRequest,
    UpworkAnalysisResponse,
    VSCodeAutomationRequest,
    VSCodeAutomationResponse,
    ChatSearchHandler,
    UpworkAnalyzerHandler,
    VSCodeAutomationHandler,
    CapabilityRouter,
)

__all__ = [
    "ChatSearchRequest",
    "ChatSearchResponse",
    "UpworkAnalysisRequest",
    "UpworkAnalysisResponse",
    "VSCodeAutomationRequest",
    "VSCodeAutomationResponse",
    "ChatSearchHandler",
    "UpworkAnalyzerHandler",
    "VSCodeAutomationHandler",
    "CapabilityRouter",
]
