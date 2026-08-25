from runtime_v2.services.fallback_manager import get_live_fallbacks, _get_deepseek_direct_fallback, _get_opencode_fallback

import asyncio

async def test():
    fallbacks = await get_live_fallbacks(mode='auto')
    print(f'Live fallbacks count: {len(fallbacks)}')
    for f in fallbacks[:5]:
        print(f'  model: {f["model"]}, provider: {f["provider"]}, pricing: {f["pricing"]}')
    
    deepseek = _get_deepseek_direct_fallback()
    print(f'DeepSeek direct: {len(deepseek)} entries')
    
    opencode = _get_opencode_fallback()
    print(f'OpenCode fallback: {len(opencode)} entries')

asyncio.run(test())