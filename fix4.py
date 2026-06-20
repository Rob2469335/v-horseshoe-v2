f = open(r"C:\Users\rober\Projects\v-horseshoe-v2\swarm_os\services\agent_service.py", encoding="utf-8")
lines = f.readlines()
f.close()

new = [
    "        system_msg = (\n",
    "            \"You are a precise AI assistant. Use tools by outputting this exact format:\\n\"\n",
    "            \"<tool_call name=\\\"tool_name\\\">{\\\"key\\\": \\\"value\\\"}</tool_call>\\n\\n\"\n",
    "            \"Available tools: web_search({query}), filesystem({operation, path}), \"\n",
    "            \"vscode_automation({command, args}), qdrant_recall({query, collection})\\n\"\n",
    "            \"After receiving an Observation, give your final answer as plain text.\\n\"\n",
    "        )\n",
]

lines[130:142] = new

open(r"C:\Users\rober\Projects\v-horseshoe-v2\swarm_os\services\agent_service.py", "w", encoding="utf-8").writelines(lines)
print("Written OK")
