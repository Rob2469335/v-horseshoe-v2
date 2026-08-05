import json
import httpx
from datetime import datetime

LLAMA_URL = "http://127.0.0.1:8080/v1/chat/completions"
MODEL = "qwen3.5-4b"

# UPGRADE: pooled client reused across all 6 request paths (avoids a fresh
# TLS/DNS handshake + connection per proposal/estimate/scope/invoice request).
_client: httpx.AsyncClient | None = None

def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=120.0, write=60.0, pool=10.0),
            headers={"Authorization": "Bearer llama"},
        )
    return _client

async def run_upwork_task(task_type: str, user_input: str):
    """
    Decision engine layer:
    - retrieves memory from Qdrant
    - queries local Llama LLM with structured prompts
    - returns structured output matching front-end expectations
    """
    # BUG FIX: was `search_similar([0.0]*768, limit=5)` — a zero-vector embedding
    # query that returned garbage "matches" purely to populate a count. The
    # `memory_used` field is a diagnostic count, not a retrieval feature, so report
    # the number of stored memories via a real collection count (fails open to 0 —
    # never raises, never blocks the proposal generation).
    memory_count = 0
    try:
        from swarm_os.upwork.learning_engine_qdrant import COLLECTION, client
        if hasattr(client, "count"):
            _count = client.count(collection_name=COLLECTION, exact=True)
            memory_count = int(getattr(_count, "count", 0) or 0)
    except Exception:
        memory_count = 0

    try:
        if task_type == "propose":
            prompt = (
                "You are an expert Upwork proposal and cover letter writer.\n"
                f"Analyze this client's job requirements:\n{user_input}\n\n"
                "Write a professional, compelling cover letter that addresses the client's needs directly. "
                "Highlight relevant experience in React, TypeScript, Python, FastAPI, and agentic AI systems. "
                "The freelancer's name is Robert. Keep it conversational, tailored, and high-impact. Do not use placeholders.\n\n"
                "Return a JSON object with a single key 'content' containing the generated text."
            )
            r = await _get_client().post(LLAMA_URL, json={"model": MODEL, "messages": [{"role": "user", "content": prompt}], "response_format": {"type": "json_object"}})
            data = r.json()
            try:
                response_json = json.loads(data.get("choices", [{}])[0].get("message", {}).get("content", "{}"))
            except Exception:
                response_json = {}
            content = response_json.get("content", data.get("choices", [{}])[0].get("message", {}).get("content", ""))
            return {
                "type": "proposal",
                "content": content,
                "memory_used": memory_count,
                "timestamp": datetime.utcnow().isoformat()
            }

        elif task_type == "rate":
            prompt = (
                "You are an expert Upwork contract estimator and bid strategist.\n"
                f"Analyze this job posting:\n{user_input}\n\n"
                "Provide an estimation of:\n"
                "1. Total hours range (e.g., '10-20' or '40-60').\n"
                "2. Recommended bid range/amount in USD (e.g., '$400-$700' or '$1,500').\n"
                "3. Analysis of the complexity, potential roadblocks, and estimation reasoning.\n\n"
                "Return a JSON object with keys 'hours', 'bid', and 'analysis'."
            )
            r = await _get_client().post(LLAMA_URL, json={"model": MODEL, "messages": [{"role": "user", "content": prompt}], "response_format": {"type": "json_object"}})
            data = r.json()
            try:
                response_json = json.loads(data.get("choices", [{}])[0].get("message", {}).get("content", "{}"))
            except Exception:
                response_json = {}
            return {
                "type": "estimate",
                "hours": response_json.get("hours", "10-20"),
                "bid": response_json.get("bid", "$500"),
                "analysis": response_json.get("analysis", "No analysis provided."),
                "timestamp": datetime.utcnow().isoformat()
            }

        elif task_type == "scope":
            prompt = (
                "You are an expert technical product manager.\n"
                f"Analyze this job posting:\n{user_input}\n\n"
                "Define a technical roadmap for this project, including:\n"
                "1. A list of 4-6 development milestones or concrete steps.\n"
                "2. Total duration estimate (e.g., '2 weeks' or '1 month').\n\n"
                "Return a JSON object with keys:\n"
                "- 'items': a list of strings representing milestones.\n"
                "- 'estimate': a string representing the duration."
            )
            r = await _get_client().post(LLAMA_URL, json={"model": MODEL, "messages": [{"role": "user", "content": prompt}], "response_format": {"type": "json_object"}})
            data = r.json()
            try:
                response_json = json.loads(data.get("choices", [{}])[0].get("message", {}).get("content", "{}"))
            except Exception:
                response_json = {}
            return {
                "type": "scope_breakdown",
                "items": response_json.get("items", ["Backend routing", "Frontend UI", "Testing & verification"]),
                "estimate": response_json.get("estimate", "2-3 weeks")
            }

        elif task_type == "invoice":
            prompt = (
                "You are an expert freelance accountant.\n"
                f"Analyze this job posting:\n{user_input}\n\n"
                "Generate a draft invoice summary listing logical milestones/deliverables with estimated prices in USD, and a final total.\n\n"
                "Return a JSON object with keys:\n"
                "- 'summary': a text summary listing the deliverables and pricing.\n"
                "- 'total': the total estimated price in USD (e.g., '$750')."
            )
            r = await _get_client().post(LLAMA_URL, json={"model": MODEL, "messages": [{"role": "user", "content": prompt}], "response_format": {"type": "json_object"}})
            data = r.json()
            try:
                response_json = json.loads(data.get("choices", [{}])[0].get("message", {}).get("content", "{}"))
            except Exception:
                response_json = {}
            return {
                "type": "invoice",
                "summary": response_json.get("summary", "Project setup and delivery"),
                "total": response_json.get("total", "$500")
            }

        elif task_type == "pitch":
            prompt = (
                "You are an expert developer pitching past case studies to a client.\n"
                f"Analyze this job posting:\n{user_input}\n\n"
                "Generate 3-5 high-impact bullet points of past accomplishments matching this job's needs. "
                "Use the structure of: Problem solved, System built, and Result delivered for each bullet point.\n\n"
                "Return a JSON object with a key 'bullets' containing a list of strings."
            )
            r = await _get_client().post(LLAMA_URL, json={"model": MODEL, "messages": [{"role": "user", "content": prompt}], "response_format": {"type": "json_object"}})
            data = r.json()
            try:
                response_json = json.loads(data.get("choices", [{}])[0].get("message", {}).get("content", "{}"))
            except Exception:
                response_json = {}
            return {
                "type": "case_study",
                "bullets": response_json.get("bullets", ["Built scalable microservices", "Optimized React render speeds by 40%"])
            }

        elif task_type == "skills_gap":
            prompt = (
                "You are an expert technical auditor analyzing a job requirements specification.\n"
                f"Analyze this job posting:\n{user_input}\n\n"
                "Identify 3-5 key advanced/niche skills required for this job that a standard full-stack developer "
                "might not possess or might need extra preparation for.\n\n"
                "Return a JSON object with a key 'missing' containing a list of strings representing these skills."
            )
            r = await _get_client().post(LLAMA_URL, json={"model": MODEL, "messages": [{"role": "user", "content": prompt}], "response_format": {"type": "json_object"}})
            data = r.json()
            try:
                response_json = json.loads(data.get("choices", [{}])[0].get("message", {}).get("content", "{}"))
            except Exception:
                response_json = {}
            return {
                "type": "gap_analysis",
                "missing": response_json.get("missing", ["Custom LLM integration", "Advanced state caching"])
            }

    except Exception as exc:
        return {"error": f"Local LLM query failed: {exc}"}

    return {"error": "unknown task"}

