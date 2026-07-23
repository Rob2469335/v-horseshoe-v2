import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen3:14b"


async def explain_decision(job_text: str, prediction: dict):

    prompt = f"""
You are an Upwork bidding strategist.

Given:
Job: {job_text}

Prediction:
{prediction}

Do 3 things:
1. Explain why win probability is high/low
2. Suggest bid strategy
3. Suggest how to improve proposal

Be concise and practical.
"""

    import httpx
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(OLLAMA_URL, json={
            "model": MODEL,
            "prompt": prompt,
            "stream": False
        })

    return r.json().get("response", "")
