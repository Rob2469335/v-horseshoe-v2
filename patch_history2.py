path = r'C:\Users\rober\Projects\v-horseshoe-v2\swarm_os\services\agent_service.py'
with open(path, 'r', encoding='utf-8') as f:
    src = f.read()

old = '        messages = [{"role": "system", "content": system_msg}] + context_messages + history'
new = '        # Trim history to last 8 messages to avoid context limit 400 errors\n        trimmed_history = history[-8:] if len(history) > 8 else history\n        messages = [{"role": "system", "content": system_msg}] + context_messages + trimmed_history'

if old in src:
    src = src.replace(old, new)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(src)
    print('Patched')
else:
    print('ERROR: not found')
