from pydantic import BaseModel, Field
from typing import List, Dict, Optional

class ChatSearchRequest(BaseModel):
    query: str = Field(..., description="The semantic or keyword query to run against chat histories.")
    max_results: int = Field(5, ge=1, le=50, description="Maximum number of historical logs to retrieve.")

class ChatMessageResult(BaseModel):
    timestamp: str
    sender: str
    message: str
    score: float = Field(..., description="Relevance ranking score.")

class ChatSearchResponse(BaseModel):
    status: str = "success"
    query: str
    results: List[ChatMessageResult] = Field(default_factory=list)
    message: Optional[str] = None

class UpworkAnalysisRequest(BaseModel):
    job_description: str = Field(..., description="The raw text block from the Upwork posting.")

class RecommendedBid(BaseModel):
    projected_rate: str
    required_tokens_estimate: int

class UpworkAnalysisResponse(BaseModel):
    status: str = "success"
    primary_domain: str
    match_score: float
    fit_metrics: Dict[str, float]
    domain_matches: Dict[str, List[str]]
    recommended_bid: Optional[RecommendedBid] = None
    should_bid: bool

class VSCodeAutomationRequest(BaseModel):
    command: str = Field(..., description="Allowed workspace operation (e.g., test, format, list_files).")
    args: List[str] = Field(default_factory=list, description="Sanitized arguments targeting local code context.")

class VSCodeAutomationResponse(BaseModel):
    status: str
    command: str
    stdout: str
    stderr: str
    exit_code: int
    message: Optional[str] = None
