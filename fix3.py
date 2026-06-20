f = open(r"C:\Users\rober\Projects\v-horseshoe-v2\swarm_os\services\agent_service.py", encoding="utf-8")
lines = f.readlines()
f.close()

new = [
    "                msg = (\n",
    "                    \"You are a precise AI assistant. Use tools by outputting:\\n\"\n",
    "                    \"<tool_call name=\\\"tool\\\">{\\\"key\\\": \\\"val\\\"}</tool_call>\\n\"\n",
    "                    \"Tools: web_search, filesystem, vscode_automation, qdrant_recall\\n\"\n",
    "                )\n",
    "                return msg\n",
]
lines[130:141] = new
open(r"C:\Users\rober\Projects\v-horseshoe-v2\swarm_os\services\agent_service.py", "w", encoding="utf-8").writelines(lines)
print("OK")
