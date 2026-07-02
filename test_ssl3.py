import ssl
ssl._create_default_https_context = ssl._create_unverified_context
ssl.create_default_context = ssl._create_unverified_context
import asyncio, litellm
async def test():
    try:
        await litellm.acompletion(model='groq/llama-3.1-8b-instant', messages=[{'role':'user','content':'hi'}])
        print('SUCCESS')
    except Exception as e:
        print('CAUGHT:', type(e), e)
asyncio.run(test())
