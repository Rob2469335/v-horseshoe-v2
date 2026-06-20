import re
path = r'C:\Users\rober\Projects\v-horseshoe-v2\swarm_os\services\agent_service.py'
with open(path, 'r', encoding='utf-8') as f:
    src = f.read()

# 1. Expand agent roster with full 2027 swarm
old_agents = '''    def _setup_default_agents(self) -> None:
        roles = {
            "coordinator": {"role": "coordinator", "description": "Main orchestrator for task delegation and context management.", "model_role": "reasoning"},
            "planner":     {"role": "planner",     "description": "Breaks down complex prompts into actionable plans.",           "model_role": "reasoning"},
            "executor":    {"role": "executor",    "description": "Executes specific plan steps and aggregates results.",          "model_role": "reasoning"},
            "tool-runner": {"role": "tool-runner", "description": "Specialized agent for executing capability and tool calls.",    "model_role": "coder_small"},
        }
        for agent_id, config in roles.items():
            self.register_agent(agent_id, config)'''

new_agents = '''    def _setup_default_agents(self) -> None:
        roles = {
            "coordinator": {"role": "coordinator", "description": "Supreme orchestrator. Analyzes intent, delegates to specialists, synthesizes final response.", "model_role": "planner"},
            "planner":     {"role": "planner",     "description": "Decomposes complex tasks into ordered execution steps with dependencies and success criteria.", "model_role": "planner"},
            "executor":    {"role": "executor",    "description": "Executes plan steps autonomously using tools. Reads files, runs searches, writes code, patches files.", "model_role": "deep_coder"},
            "researcher":  {"role": "researcher",  "description": "Deep research agent. Searches web, reads docs, synthesizes findings into actionable intelligence.", "model_role": "reasoning"},
            "coder":       {"role": "coder",       "description": "Elite software engineer. Writes, refactors, debugs, and optimizes code with surgical precision.", "model_role": "deep_coder"},
            "reviewer":    {"role": "reviewer",    "description": "Critical reviewer. Validates code quality, security, performance, and correctness.", "model_role": "reasoning"},
            "tool-runner": {"role": "tool-runner", "description": "Specialized agent for raw tool execution and capability calls.", "model_role": "coder"},
        }
        for agent_id, config in roles.items():
            self.register_agent(agent_id, config)'''

src = src.replace(old_agents, new_agents)

# 2. Wire delegate to actually run the target agent
old_delegate = '''            if mapped_tool_name == "__delegate__":
                logger.warning(f"[DELEGATE PAYLOAD] {payload}")
                target = payload.get("target_agent", "")
                task = payload.get("task") or payload.get("content") or payload.get("message") or payload.get("instruction") or str(payload)
                delegate_msg = f"**Delegating to {target}**\\n\\n{task}" if target else task
                logger.info(f"[DELEGATE] Model '{active_model}' delegated to {target}: {task}")
                yield {"agent_id": agent_id, "type": "final", "model": active_model, "provider": _provider_for_model(active_model), "content": delegate_msg}
                return'''

new_delegate = '''            if mapped_tool_name == "__delegate__":
                logger.warning(f"[DELEGATE PAYLOAD] {payload}")
                target = payload.get("target_agent", "executor")
                task = payload.get("task") or payload.get("content") or payload.get("message") or payload.get("instruction") or str(payload)
                logger.info(f"[DELEGATE] Coordinator -> {target}: {task}")
                # Yield handoff event so CLI shows delegation
                yield {"agent_id": agent_id, "type": "agent_handoff", "from": agent_id, "to": target, "task": task}
                # Run the target agent with the delegated task
                if target in self.agents:
                    delegate_history = list(messages)
                    async for sub_chunk in self.step_agent_stream(target, task, history=delegate_history):
                        # Re-tag with original agent_id for CLI continuity
                        sub_chunk["delegated_by"] = agent_id
                        yield sub_chunk
                else:
                    yield {"agent_id": agent_id, "type": "final", "model": active_model, "provider": _provider_for_model(active_model), "content": f"Agent '{target}' not found. Task: {task}"}
                return'''

src = src.replace(old_delegate, new_delegate)

# 3. Build per-agent system prompts
old_system = '''    def _build_system_instruction(self, agent: Dict[str, Any]) -> str:
        instruction = [
            f"You are Zenith (ID: {agent['id']}), a senior agentic software engineer.",
            f"Role: {agent['role'].upper()} - {agent['description']}",
            "",
            "CORE MANDATES:",
            "1. Tool Supremacy: NEVER explain what you will do. ALWAYS use a tool first.",
            "2. Surgical Precision: Use 'filesystem' with 'patch' for code edits.",
            "3. Autonomous Research: Use 'vscode_automation' (scout/grep/ls) to verify state.",
            "4. Groundedness: Use 'web_search' for any external information.",
            "",
            "TOOL CALL FORMAT:",
            'Output EXACTLY this on its own line to call a tool:',
            '<tool_call name="tool_name">{"param": "value"}</tool_call>',
            "",
            "AVAILABLE TOOLS:",
            "- filesystem: {operation: \'read\'|\'write\'|\'patch\'|\'list\', path: \'...\', content?: \'...\'}",
            "- vscode_automation: {command: \'grep\'|\'ls\'|\'cat\'|\'scout\', args: [...]}",
            "- web_search: {query: \'...\'}",
            "- ask_user: {question: \'...\', options?: [...]}",
            "",
            "Think step-by-step. Be concise. Be agentic.",
        ]
        return "\\n".join(instruction)'''

new_system = '''    def _build_system_instruction(self, agent: Dict[str, Any]) -> str:
        role = agent.get("role", "general")
        agent_id = agent.get("id", "agent")
        desc = agent.get("description", "")

        base = [
            f"You are Zenith-{agent_id.upper()}, an autonomous AI agent in the Swarm OS.",
            f"Role: {role.upper()} — {desc}",
            "",
            "TOOL CALL FORMAT (output EXACTLY, no explanation before calling):",
            \'<tool_call name="tool_name">{"param": "value"}</tool_call>\',
            "",
            "AVAILABLE TOOLS:",
            "- filesystem: {operation: \'read\'|\'write\'|\'patch\'|\'list\', path: \'...\', content?: \'...\'}",
            "- vscode_automation: {command: \'grep\'|\'ls\'|\'cat\'|\'scout\', args: [...]}",
            "- web_search: {query: \'...\'}",
            "- ask_user: {question: \'...\', options?: [...]}",
            "",
        ]

        role_mandates = {
            "coordinator": [
                "COORDINATOR MANDATES:",
                "1. Analyze the user request deeply.",
                "2. Use vscode_automation to scan the codebase first.",
                "3. Produce a direct, comprehensive response.",
                "4. Only delegate if the task requires specialized execution.",
                "5. NEVER delegate simple questions — answer them directly.",
            ],
            "planner": [
                "PLANNER MANDATES:",
                "1. Break the task into numbered steps with clear success criteria.",
                "2. Identify dependencies between steps.",
                "3. Output a structured <plan> block.",
                "4. Be specific — name files, functions, and expected outputs.",
            ],
            "executor": [
                "EXECUTOR MANDATES:",
                "1. Execute tasks autonomously using tools.",
                "2. Read files before editing them.",
                "3. Use grep/ls to understand structure before acting.",
                "4. Write complete, working code — no placeholders.",
                "5. Verify your changes after making them.",
                "6. Report results clearly when done.",
            ],
            "coder": [
                "CODER MANDATES:",
                "1. Write production-quality code only.",
                "2. Read existing code before writing new code.",
                "3. Use filesystem patch for surgical edits.",
                "4. Include error handling in all code.",
                "5. Follow existing code style and patterns.",
            ],
            "researcher": [
                "RESEARCHER MANDATES:",
                "1. Search for current, authoritative information.",
                "2. Cross-reference multiple sources.",
                "3. Synthesize findings into clear, actionable intelligence.",
                "4. Always cite sources in your response.",
            ],
            "reviewer": [
                "REVIEWER MANDATES:",
                "1. Read the code or content being reviewed.",
                "2. Check for bugs, security issues, and performance problems.",
                "3. Provide specific, actionable feedback with line references.",
                "4. Rate overall quality and list top 3 improvements.",
            ],
        }

        mandates = role_mandates.get(role, [
            "CORE MANDATES:",
            "1. Use tools to gather information before responding.",
            "2. Be direct and precise.",
            "3. Complete the task fully.",
        ])

        instruction = base + mandates + ["", "Think autonomously. Act decisively. Report clearly."]
        return "\\n".join(instruction)'''

src = src.replace(old_system, new_system)

with open(path, 'w', encoding='utf-8') as f:
    f.write(src)
print('Done — full 2027 swarm wired up')
