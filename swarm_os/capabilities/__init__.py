"""
Capabilities module for the Horseshoe Swarm autonomous agent platform.
Provides modular capability handlers with a central routing system.
"""

from swarm_os.capabilities.models import (
    ChatSearchRequest,
    ChatSearchResponse,
    UpworkAnalysisRequest,
    UpworkAnalysisResponse,
    VSCodeAutomationRequest,
    VSCodeAutomationResponse,
)
from swarm_os.capabilities.chat_search import ChatSearchHandler
from swarm_os.capabilities.upwork_analyzer import UpworkAnalyzerHandler
from swarm_os.capabilities.vscode_automation import VSCodeAutomationHandler
from swarm_os.capabilities.capability_router import CapabilityRouter

__all__ = [
    # Request/Response Models
    "ChatSearchRequest",
    "ChatSearchResponse",
    "UpworkAnalysisRequest",
    "UpworkAnalysisResponse",
    "VSCodeAutomationRequest",
    "VSCodeAutomationResponse",
    # Handler Classes
    "ChatSearchHandler",
    "UpworkAnalyzerHandler",
    "VSCodeAutomationHandler",
    # Router
    "CapabilityRouter",
]
