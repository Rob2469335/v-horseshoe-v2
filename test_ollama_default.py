import os, ssl, asyncio, litellm
ssl._create_default_https_context = ssl._create_unverified_context
ssl.create_default_context = ssl._create_unverified_context
if 'OLLAMA_API_BASE' in os.environ: del os.environ['OLLAMA_API_BASE']
async def test():
    try:
        await litellm.acompletion(model='ollama/qwen3:14b', messages=[{'role':'user','content':'hi'}])
    except Exception as e:
        print('FINAL EXCEPTION:', type(e), e)
asyncio.run(test())
