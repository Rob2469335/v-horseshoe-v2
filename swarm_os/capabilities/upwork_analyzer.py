import logging
from typing import Dict, Any, List
from swarm_os.capabilities.models import UpworkAnalysisRequest, UpworkAnalysisResponse, RecommendedBid

logger = logging.getLogger(__name__)

class UpworkAnalyzerHandler:
    def __init__(self, rules: Dict[str, Any] = None):
        self.rules = rules or {
            "domains": {
                "coding": ["python", "development", "backend", "api", "pytest", "automation"],
                "general": ["management", "writing", "virtual assistant", "support"]
            },
            "min_score_to_bid": 0.4
        }
        logger.info("Initialized operational UpworkAnalyzerHandler.")

    async def analyze_job(self, payload: UpworkAnalysisRequest) -> UpworkAnalysisResponse:
        desc_lower = payload.job_description.lower().strip()

        if not desc_lower:
            return UpworkAnalysisResponse(
                status="success",
                primary_domain="general",
                match_score=0.0,
                fit_metrics={"coding": 0.0, "general": 0.0},
                domain_matches={"coding": [], "general": []},
                should_bid=False,
                recommended_bid=None
            )

        matches: Dict[str, List[str]] = {}
        scores: Dict[str, float] = {}

        domains = self.rules.get("domains", {})
        for domain, keywords in domains.items():
            matched_words = [kw for kw in keywords if kw in desc_lower]
            matches[domain] = matched_words
            scores[domain] = round(len(matched_words) / max(len(keywords), 1), 2)

        if not scores:
            return UpworkAnalysisResponse(
                status="success",
                primary_domain="general",
                match_score=0.0,
                fit_metrics={},
                domain_matches=matches,
                should_bid=False,
                recommended_bid=None
            )

        primary_domain = max(scores, key=scores.get)
        match_score = scores.get(primary_domain, 0.0)
        should_bid = match_score >= self.rules.get("min_score_to_bid", 0.4)

        rec_bid = None
        if should_bid:
            projected_rate = "$75/hr" if primary_domain == "coding" else "$25/hr"
            rec_bid = RecommendedBid(
                projected_rate=projected_rate,
                required_tokens_estimate=1200
            )

        return UpworkAnalysisResponse(
            status="success",
            primary_domain=primary_domain,
            match_score=match_score,
            fit_metrics=scores,
            domain_matches=matches,
            should_bid=should_bid,
            recommended_bid=rec_bid
        )
