import asyncio, time
from litellm import acompletion

async def main():
    try:
        res = await acompletion(
            model='ollama_chat/qwen3:4b-instruct',
            messages=[{'role': 'user', 'content': 'say hello'}],
            timeout=600
        )
        print('Success:', res.choices[0].message.content)
    except Exception as e:
        print('Error:', type(e), str(e))

asyncio.run(main())
