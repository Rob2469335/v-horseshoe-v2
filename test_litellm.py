import asyncio, litellm
async def test():
    try:
        await litellm.acompletion(model='ollama/qwen3:14b', fallbacks=['groq/llama-3.1-8b-instant'], messages=[{'role':'user','content':'hi'}])
        print('SUCCESS')
    except Exception as e:
        print('CAUGHT:', type(e), e)
asyncio.run(test())
