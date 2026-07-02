import os
os.environ['LITELLM_VERIFY_SSL'] = 'False'
import asyncio, litellm
async def test():
    try:
        await litellm.acompletion(model='groq/llama-3.1-8b-instant', messages=[{'role':'user','content':'hi'}])
        print('SUCCESS')
    except Exception as e:
        print('CAUGHT:', type(e), e)
asyncio.run(test())
