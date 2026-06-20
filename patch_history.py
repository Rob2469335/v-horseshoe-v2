path = r'C:\Users\rober\Projects\v-horseshoe-v2\swarm_os\services\agent_service.py'
with open(path, 'r', encoding='utf-8') as f:
    src = f.read()

old = '        system_msg = self._build_system_instruction(agent)\n        user_prompt = _build_ctx(prompt) if prompt else prompt\n        messages = [{"role": "system", "content": system_msg}] + history'

new = '        system_msg = self._build_system_instruction(agent)\n        user_prompt = _build_ctx(prompt) if prompt else prompt\n        # Trim history to last 10 messages to avoid context limit 400 errors\n        trimmed_history = history[-10:] if len(history) > 10 else history\n        messages = [{"role": "system", "content": system_msg}] + trimmed_history'

if old in src:
    src = src.replace(old, new)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(src)
    print('Patched')
else:
    print('ERROR: not found')
