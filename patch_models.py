import pathlib, shutil, py_compile, sys

TARGET = pathlib.Path("swarm_os/services/agent_service.py")
if not TARGET.exists():
    print("ERROR: not found"); sys.exit(1)

BACKUP = TARGET.with_suffix(".py.bak_models")
shutil.copy2(TARGET, BACKUP)
print("Backup -> {}".format(BACKUP))

src = TARGET.read_text(encoding="utf-8")
original = src

# FIX: Replace the entire fallback_chain construction block.
# Currently it builds a 30+ item chain starting with broken cloud models.
# Replace with a tight, working chain based on what actually passes tool-call compliance:
#   coordinator/planner/reviewer -> mistral-nemo:12b (best instruction following locally)
#   executor/coder/tool-runner  -> qwen2.5-coder:7b (fast, code-focused, passes test)
# Gemini goes second if key works, OpenRouter free goes third as last resort.

OLD = """        if agent_id == "coordinator":
            fallback_chain = [("qwen2.5:7b-instruct", "ollama")]
        elif agent_id in ("executor", "tool-runner"):
            fallback_chain = [("qwen2.5-coder:7b", "ollama")]
        else:
            fallback_chain = []"""

NEW = """        # Pin each agent role to models proven to follow tool-call format.
        # coordinator/planner/reviewer: mistral-nemo:12b - best local instruction following
        # executor/coder/tool-runner: qwen2.5-coder:7b - fast, code-focused
        if agent_id in ("coordinator", "planner", "reviewer"):
            fallback_chain = [
                ("mistral-nemo:12b", "ollama"),
                ("gemini-2.5-flash", "gemini"),
                ("qwen2.5-coder:7b", "ollama"),
            ]
        else:
            fallback_chain = [
                ("qwen2.5-coder:7b", "ollama"),
                ("gemini-2.5-flash", "gemini"),
                ("mistral-nemo:12b", "ollama"),
            ]"""

if OLD not in src:
    print("ERROR: fallback_chain anchor not found")
    sys.exit(1)

src = src.replace(OLD, NEW, 1)
print("Fix 1 applied: agent model pinning")

# FIX 2: Skip the entire OR/Groq/NVIDIA live-model population since all 400/401.
# Replace the giant fallback_chain append block with a short skip.
# Find where it starts adding OR free candidates and replace the whole block
# with just the local models we know work.
OLD2 = """        predefined_or_free = [
            ("deepseek/deepseek-chat-v3-5:free", "openrouter"),
            ("openrouter/free", "openrouter"),
            ("qwen/qwen3-coder:free", "openrouter"),
            ("deepseek/deepseek-v4-flash:free", "openrouter"),
        ]"""

NEW2 = """        # Cloud free models disabled - all returning 400/401. Using local models only.
        # Re-enable by adding working models back to fallback_chain above.
        predefined_or_free = []"""

if OLD2 not in src:
    print("WARNING: OR free anchor not found - skipping Fix 2")
else:
    src = src.replace(OLD2, NEW2, 1)
    print("Fix 2 applied: disabled broken OR free models")

# FIX 3: Also fix fetch_live_models_if_needed so sync monkeypatch in tests works.
OLD3 = "            await fetch_live_models_if_needed()"
NEW3 = """            _fm = fetch_live_models_if_needed()
            import inspect as _ins
            if _ins.isawaitable(_fm):
                await _fm"""

if OLD3 not in src:
    print("WARNING: fetch anchor not found - skipping Fix 3")
else:
    src = src.replace(OLD3, NEW3, 1)
    print("Fix 3 applied: fetch_live_models handles sync/async (fixes tests too)")

TARGET.write_text(src, encoding="utf-8")
try:
    py_compile.compile(str(TARGET), doraise=True)
    print("Syntax PASSED")
except py_compile.PyCompileError as e:
    print("SYNTAX ERROR - restoring: {}".format(e))
    shutil.copy2(BACKUP, TARGET)
    sys.exit(1)

if src == original:
    print("WARNING: no changes made")
else:
    print("Done - restart backend then /clear and test")
