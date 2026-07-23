import json
import httpx
from datetime import datetime
from swarm_os.upwork.learning_engine_qdrant import search_similar

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
MODEL = "qwen3:14b"

async def run_upwork_task(task_type: str, user_input: str):
    """
    Decision engine layer:
    - retrieves memory from Qdrant
    - queries local Ollama LLM with structured prompts
    - returns structured output matching front-end expectations
    """
    memory = search_similar([0.0]*768, limit=5)  # placeholder embedding hook
    memory_count = len(memory)

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
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(OLLAMA_URL, json={"model": MODEL, "prompt": prompt, "format": "json", "stream": False})
            data = r.json()
            try:
                response_json = json.loads(data.get("response", "{}"))
            except Exception:
                response_json = {}
            content = response_json.get("content", data.get("response", ""))
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
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(OLLAMA_URL, json={"model": MODEL, "prompt": prompt, "format": "json", "stream": False})
            data = r.json()
            try:
                response_json = json.loads(data.get("response", "{}"))
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
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(OLLAMA_URL, json={"model": MODEL, "prompt": prompt, "format": "json", "stream": False})
            data = r.json()
            try:
                response_json = json.loads(data.get("response", "{}"))
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
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(OLLAMA_URL, json={"model": MODEL, "prompt": prompt, "format": "json", "stream": False})
            data = r.json()
            try:
                response_json = json.loads(data.get("response", "{}"))
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
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(OLLAMA_URL, json={"model": MODEL, "prompt": prompt, "format": "json", "stream": False})
            data = r.json()
            try:
                response_json = json.loads(data.get("response", "{}"))
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
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(OLLAMA_URL, json={"model": MODEL, "prompt": prompt, "format": "json", "stream": False})
            data = r.json()
            try:
                response_json = json.loads(data.get("response", "{}"))
            except Exception:
                response_json = {}
            return {
                "type": "gap_analysis",
                "missing": response_json.get("missing", ["Custom LLM integration", "Advanced state caching"])
            }

    except Exception as exc:
        return {"error": f"Ollama query failed: {exc}"}

    return {"error": "unknown task"}

