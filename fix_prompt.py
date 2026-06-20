lines = open(r'C:\Users\rober\Projects\v-horseshoe-v2\swarm_os\services\agent_service.py', encoding='utf-8').readlines()

new_prompt = [
    '                "You are a precise AI assistant. Use tools when needed.\n"\n',
    '                "TOOL FORMAT - use EXACTLY this on its own line:\n"\n',
    '                "<tool_call name=\\"tool_name\\">{\\"param\\": \\"value\\"}</tool_call>\n\n"\n',
    '                "RULES:\n"\n',
    '                "1. Never use XML attribute format like tag attr=value\n"\n',
    '                "2. After Observation, give final answer as plain text\n\n"\n',
    '                "TOOLS:\n"\n',
    '                "- web_search: {query: str}\n"\n',
    '                "- filesystem: {operation: read|list, path: str}\n"\n',
    '                "- vscode_automation: {command: scout|grep|cat, args: list}\n"\n',
    '                "- qdrant_recall: {query: str, collection: codebase}\n"\n',
]

lines[130:141] = new_prompt
open(r'C:\Users\rober\Projects\v-horseshoe-v2\swarm_os\services\agent_service.py', 'w', encoding='utf-8').writelines(lines)
print('Written OK')
