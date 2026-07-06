import asyncio, time
from litellm import acompletion

async def main():
    print('Starting...')
    start = time.time()
    try:
        res = await acompletion(
            model='ollama_chat/qwen3:4b-instruct',
            messages=[{'role': 'user', 'content': 'output json only: {\"action\": \"test\"}'}],
            timeout=600,
            format='json'
        )
        print('Success:', res.choices[0].message.content)
    except Exception as e:
        print('Error:', e)
    print(f'Time: {time.time() - start:.2f}s')

asyncio.run(main())
