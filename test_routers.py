import asyncio
import litellm
import httpx
import logging

logging.basicConfig(level=logging.ERROR)

# Monkey-patch httpx to ALWAYS disable SSL verify
_original_init = httpx.AsyncClient.__init__
def _patched_init(self, *args, **kwargs):
    kwargs['verify'] = False
    _original_init(self, *args, **kwargs)
httpx.AsyncClient.__init__ = _patched_init

# Also patch aiohttp just in case litellm uses it
try:
    import aiohttp
    _orig_tcp = aiohttp.TCPConnector.__init__
    def _patched_tcp(self, *args, **kwargs):
        kwargs['verify_ssl'] = False
        _orig_tcp(self, *args, **kwargs)
    aiohttp.TCPConnector.__init__ = _patched_tcp
except ImportError:
    pass

async def test_all():
    providers = {
        'OpenRouter (Llama 3.3 70B)': 'openrouter/meta-llama/llama-3.3-70b-instruct:free',
        'Gemini (Flash)': 'gemini/gemini-1.5-flash',
        'Ollama (Qwen 3.5 9B)': 'ollama/llama3-groq-tool-use:8b',
    }
    
    for name, model in providers.items():
        print(f"Testing {name}...")
        try:
            api_base = 'http://127.0.0.1:11434' if name.startswith('Ollama') else None
            response = await litellm.acompletion(
                model=model,
                messages=[{'role': 'user', 'content': 'Hello, are you there? Please reply with exactly YES.'}],
                api_base=api_base,
                max_tokens=10
            )
            print(f"  -> SUCCESS! Response: {response.choices[0].message.content.strip()}\n")
        except Exception as e:
            print(f"  -> FAILED: {type(e).__name__} - {str(e)}\n")

if __name__ == '__main__':
    asyncio.run(test_all())

