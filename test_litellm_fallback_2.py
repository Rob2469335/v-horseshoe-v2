import os, ssl, asyncio, litellm
ssl._create_default_https_context = ssl._create_unverified_context
ssl.create_default_context = ssl._create_unverified_context
os.environ['GROQ_API_KEY'] = 'gsk_bad_key'
async def test():
    try:
        await litellm.acompletion(model='ollama/qwen3:14b', fallbacks=['groq/llama-3.1-8b-instant'], messages=[{'role':'user','content':'hi'}])
    except Exception as e:
        print('FINAL EXCEPTION:', type(e), e)
asyncio.run(test())
