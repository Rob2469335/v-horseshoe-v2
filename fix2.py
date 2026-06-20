f = r'C:\Users\rober\Projects\v-horseshoe-v2\swarm_os\services\agent_service.py'
lines = open(f, encoding='utf-8').readlines()
sys_prompt = (
    '                "You are a precise AI. Use the tool format below when calling tools.\\n"\n'
    '                "Format: <tool_call name=\\"name\\">{\\"key\\":\\"val\\"}</tool_call>\\n"\n'
    '                "Tools: web_search(query), filesystem(operation,path), vscode_automation(command,args), qdrant_recall(query,collection)\\n"\n'
)
lines[130:141] = [sys_prompt]
open(f, 'w', encoding='utf-8').writelines(lines)
print('OK')
