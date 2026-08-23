import httpx
import base64
from pathlib import Path
from typing import Optional

LLAMA_BASE = "http://127.0.0.1:8083"
API_KEY = "llama"
MODEL_QWEN = "qwen3-vl-2b"
MODEL_GLM = "glm-ocr"

_client = None

def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=120.0)
    return _client

class VisionRouter:
    def __init__(self):
        self.headers = {"Authorization": f"Bearer {API_KEY}"}

    async def _load_model(self, model: str):
        print(f"[{model}] Hot-swapping into RAM...")
        resp = await get_client().post(f"{LLAMA_BASE}/models/load", json={"model": model}, headers=self.headers)
        resp.raise_for_status()

    async def _unload_model(self, model: str):
        print(f"[{model}] Unloading from RAM...")
        resp = await get_client().post(f"{LLAMA_BASE}/models/unload", json={"model": model}, headers=self.headers)
        resp.raise_for_status()

    async def _chat_completion(self, model: str, prompt: str, b64_image: str, temp: float) -> str:
        payload = {
            "model": model,
            "temperature": temp,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_image}"}}
                    ]
                }
            ]
        }
        resp = await get_client().post(f"{LLAMA_BASE}/v1/chat/completions", json=payload, headers=self.headers)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    async def analyze_ui_screenshot(self, image_path: str, goal: str) -> str:
        """
        Tool for Playwright GUI logic. Uses the resident Qwen3-VL 2B.
        """
        b64_image = base64.b64encode(Path(image_path).read_bytes()).decode("utf-8")
        return await self._chat_completion(MODEL_QWEN, goal, b64_image, temp=0.2)

    async def extract_document_region(self, image_path: str, schema: Optional[str] = None) -> str:
        """
        Tool for dense tables, forms, or receipts. Hot-swaps to GLM-OCR, runs at temp=0, and unloads.
        """
        b64_image = base64.b64encode(Path(image_path).read_bytes()).decode("utf-8")
        
        prompt = (
            "Extract the visible table exactly. Return JSON with: "
            '{ "rows": [{"rank": "", "title": "", "gross": "", "year": ""}], "uncertain_fields": [] } '
            "Do not infer missing values. Use empty strings when unreadable."
        ) if not schema else schema

        # 1. Hot swap to GLM-OCR
        await self._load_model(MODEL_GLM)
        
        try:
            # 2. Extract cleanly
            print("[glm-ocr] Extracting dense text...")
            result = await self._chat_completion(MODEL_GLM, prompt, b64_image, temp=0.0)
            return result
        finally:
            # 3. Unload to free RAM
            try:
                await self._unload_model(MODEL_GLM)
            except Exception as e:
                print(f"[glm-ocr] Failed to unload: {e}")
            
            # 4. Restore resident Qwen
            try:
                await self._load_model(MODEL_QWEN)
            except Exception as e:
                print(f"[qwen3-vl-2b] Failed to reload: {e}")
