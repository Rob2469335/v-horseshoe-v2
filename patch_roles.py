import pathlib, shutil, py_compile, sys

TARGET = pathlib.Path("swarm_os/services/agent_service.py")
if not TARGET.exists():
    print("ERROR: not found"); sys.exit(1)

BACKUP = TARGET.with_suffix(".py.bak_roles")
shutil.copy2(TARGET, BACKUP)
print("Backup -> {}".format(BACKUP))

src = TARGET.read_text(encoding="utf-8")
original = src

OLD = """        elif role_id == 'executor':
            instruction.append('EXECUTOR RULES:')
            instruction.append('- Use filesystem to read files. Use tools to execute tasks.')
            instruction.append('- When done, delegate to coder if code changes needed, else delegate to tool-runner.')
        elif role_id == 'coder':
            instruction.append('CODER RULES:')
            instruction.append('- Use filesystem patch to make code changes. Verify syntax after.')
            instruction.append('- When done, delegate to tool-runner to verify: <tool_call name="delegate">{"target_agent": "tool-runner", "task": "verify the changes"}</tool_call>')
        elif role_id == 'tool-runner':
            instruction.append('TOOL-RUNNER RULES:')
            instruction.append('- Run verification tools. Check files exist. Run tests if needed.')
            instruction.append('- When done, delegate to reviewer: <tool_call name="delegate">{"target_agent": "reviewer", "task": "review the changes"}</tool_call>')
        elif role_id in ('reviewer', 'critic'):
            instruction.append('REVIEWER RULES:')
            instruction.append('- Read the modified files. Check for bugs, correctness, and quality.')
            instruction.append('- Output a final verdict. Do NOT delegate further.')"""

NEW = """        elif role_id == 'executor':
            instruction.append('EXECUTOR RULES:')
            instruction.append('- YOU ARE EXECUTOR. NEVER delegate to executor (never delegate to yourself).')
            instruction.append('- FIRST: use tools to do your work (filesystem read, web_search, sandbox_repl).')
            instruction.append('- AFTER tool work is done: if code must be WRITTEN or PATCHED, delegate to coder.')
            instruction.append('- If no code changes needed: delegate to tool-runner.')
            instruction.append('- Delegate to coder: <tool_call name="delegate">{"target_agent": "coder", "task": "DESCRIBE WHAT TO CODE"}</tool_call>')
            instruction.append('- Delegate to tool-runner: <tool_call name="delegate">{"target_agent": "tool-runner", "task": "DESCRIBE WHAT TO VERIFY"}</tool_call>')
        elif role_id == 'coder':
            instruction.append('CODER RULES:')
            instruction.append('- YOU ARE CODER. Your only job: write or patch code using filesystem tool.')
            instruction.append('- Use filesystem read to read existing files first. Then use patch or write.')
            instruction.append('- Use sandbox_repl with language=python to verify syntax after patching.')
            instruction.append('- When done ALWAYS end with: <tool_call name="delegate">{"target_agent": "tool-runner", "task": "verify the code changes and run tests"}</tool_call>')
        elif role_id == 'tool-runner':
            instruction.append('TOOL-RUNNER RULES:')
            instruction.append('- YOU ARE TOOL-RUNNER. Run verification tools only. Never write code.')
            instruction.append('- Use sandbox_repl with language=pytest to run tests.')
            instruction.append('- Use filesystem read to check files exist and are non-empty.')
            instruction.append('- Use sandbox_repl with language=powershell for system checks.')
            instruction.append('- When done ALWAYS end with: <tool_call name="delegate">{"target_agent": "reviewer", "task": "review all work and provide final verdict"}</tool_call>')
        elif role_id in ('reviewer', 'critic'):
            instruction.append('REVIEWER RULES:')
            instruction.append('- YOU ARE REVIEWER. Your only job: read what was done and give a final verdict.')
            instruction.append('- Use filesystem read to inspect any files mentioned in the task.')
            instruction.append('- Output a final verdict summarizing what each agent did and whether it succeeded.')
            instruction.append('- DO NOT delegate further. Your response is the final output.')"""

if OLD not in src:
    print("ERROR: anchor not found - checking partial match...")
    if "EXECUTOR RULES:" in src:
        print("  EXECUTOR RULES found in file - indentation may differ")
    sys.exit(1)

src = src.replace(OLD, NEW, 1)
TARGET.write_text(src, encoding="utf-8")

try:
    py_compile.compile(str(TARGET), doraise=True)
    print("Patch applied - Syntax PASSED")
except py_compile.PyCompileError as e:
    print("SYNTAX ERROR - restoring: {}".format(e))
    shutil.copy2(BACKUP, TARGET)
    sys.exit(1)
