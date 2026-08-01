import httpx

LLAMA_URL = "http://127.0.0.1:8080/v1/chat/completions"
MODEL = "qwen3.5-9b"

# UPGRADE: pooled client (avoids fresh TLS/connection per call)
_client: httpx.AsyncClient | None = None

def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=120.0, write=60.0, pool=10.0),
            headers={"Authorization": "Bearer llama"},
        )
    return _client


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

    r = await _get_client().post(LLAMA_URL, json={
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}]
    })

    return r.json().get("choices", [{}])[0].get("message", {}).get("content", "")
