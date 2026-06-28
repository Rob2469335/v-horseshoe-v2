import pathlib, shutil, py_compile, sys

TARGET = pathlib.Path("swarm_os/services/agent_service.py")
if not TARGET.exists():
    print("ERROR: not found"); sys.exit(1)

BACKUP = TARGET.with_suffix(".py.bak_testfix")
shutil.copy2(TARGET, BACKUP)
print("Backup -> {}".format(BACKUP))

src = TARGET.read_text(encoding="utf-8")
original = src

# FIX 1: ask_user suppression emits empty final because is_duplicate check
# fires first on the tool_call content and emits a final with empty clean_content.
# Root cause: when full_chunk_content contains <tool_call name="ask_user">...</tool_call>
# the is_control_turn check marks it as control (good), so is_duplicate is skipped (good).
# But then the regex match extracts ask_user, _map_tool_call maps it,
# reconcile returns ("ask_user", payload) from the 3-arg fallback path,
# then the ask_user check at line 808 fires correctly.
# The issue: there are TWO final chunks - one from somewhere before ask_user check.
# Let's find what emits the first empty final.
# Looking at the flow: after reconcile, mapped_tool_name = "ask_user"
# VIRTUAL_TOOLS check passes (ask_user is in VIRTUAL_TOOLS now).
# Then ask_user != coordinator check fires -> yields final with correct content.
# So why is final_chunk empty? The test does:
#   final_chunk = next((c for c in chunks if c.get("type") == "final"), None)
# It gets the FIRST final. If there's an earlier empty final, that wins.
# The earlier empty final comes from: when mock streams the tool_call content,
# the stop-token heal adds </tool_call>, regex matches, reconcile maps to ask_user.
# BUT: before that, the streaming loop also triggers is_duplicate on the PREVIOUS turn.
# Actually simpler: mistral-nemo:12b is now the model but tests use mock httpx.
# The mock streams the full ask_user tool call. After streaming, full_chunk_content
# has the tool call. is_control_turn = True. previous_outputs gets __control__:0:...
# Turn 2 would repeat but there's only 1 turn (STEP_LIMIT for planner is 8).
# So no duplicate. Match finds ask_user. reconcile(4 args) returns 3-tuple fine.
# mapped_tool_name = "ask_user". VIRTUAL_TOOLS passes. ask_user check fires.
# Yields final with "Only the coordinator...". That should be chunk[1] or so.
# The test gets first final... unless something yields an empty final before.
# 
# ACTUAL ROOT CAUSE: reconcile with 4 args returns 3-tuple (name, payload, meta).
# Line 797: mapped_tool_name, payload, repair_meta = reconcile(...4 args...)  <- OK
# BUT the test patches reconcile with lambda a,b,c: ("ask_user", b) -- 3 ARGS.
# When called with 4 args, Python raises TypeError (wrong number of args).
# Falls to except TypeError at line 798 -> calls reconcile(3 args) -> ("ask_user", b).
# Unpacks as: mapped_tool_name="ask_user", payload=b. Good.
# Then VIRTUAL_TOOLS check: ask_user IS in VIRTUAL_TOOLS -> passes through.
# Then ask_user != coordinator -> yields final with correct content. 
# So it SHOULD work. Unless... the content key is missing from the yield.
# Check lines 810-816: yields {"type":"final","content":"Only the coordinator..."}
# The test checks final_chunk["content"] -- should find it.
# WAIT: there might be an approval check first. Line ~808 checks mapped_tool_name.
# But before that: runtime.is_state_changing("ask_user", payload) -> False.
# So no ApprovalRequiredError. Should reach ask_user check fine.
# 
# NEW THEORY: the mock client streams the content but the Ollama path is used
# (local model). The mock patches httpx but Ollama uses httpx too.
# BUT: mistral-nemo is now the first model. The mock patches httpx.AsyncClient.
# The Ollama request goes through httpx. So mock intercepts it. Good.
# The mock response yields the ask_user tool_call content.
# After stream: full_chunk_content = '<tool_call name="ask_user">{"question":"yes?"}</tool_call>'
# Stop-token heal: <tool_call is present, </tool_call> IS present -> no heal needed.
# is_control_turn = True (has <tool_call). 
# match = regex finds ask_user and {"question":"yes?"}
# tool_name = "ask_user", raw_payload = '{"question":"yes?"}'
# _map_tool_call("ask_user", {...}, available_tools, "planner")
# In _map_tool_call: ask_user not in {delegate,handoff,transfer,route}
# -> calls reconcile_and_repair_tool_call("ask_user", payload, available_tools, "planner")
# TEST has patched reconcile with lambda a,b,c -- 3 params. Called with 4 -> TypeError.
# Falls to except -> reconcile("ask_user", payload, available_tools) -> ("ask_user", payload)
# mapped_tool_name = "ask_user". VIRTUAL_TOOLS has ask_user -> skip unknown tool check.
# Line 808: mapped_tool_name == "ask_user" and agent_id("planner") != "coordinator" -> TRUE
# Yields {"type":"final","content":"Only the coordinator agent is allowed..."}
# return.
# 
# So the logic IS correct. The test SHOULD pass. Let me check if maybe
# the issue is that _map_tool_call itself calls reconcile and returns a TUPLE,
# and then the outer code also calls reconcile again redundantly.
# 
# Looking at agent_service.py _map_tool_call:
#   def _map_tool_call(tool_name, payload, available_tools, agent_id):
#       if tool_name in {"delegate","handoff","transfer","route"}:
#           return "__delegate__", payload
#       try:
#           reconciled = reconcile_and_repair_tool_call(tool_name, payload, available_tools, agent_id)
#       except TypeError:
#           reconciled = reconcile_and_repair_tool_call(tool_name, payload, available_tools)
#       if isinstance(reconciled, tuple):
#           if len(reconciled) >= 2: return reconciled[0], reconciled[1]
#           if len(reconciled) == 1: return reconciled[0], payload
#       return tool_name, payload
#
# So _map_tool_call returns ("ask_user", payload). 
# Then at line 797, the code ALSO calls reconcile_and_repair_tool_call directly!
# This is a DUPLICATE reconcile call. _map_tool_call already reconciled,
# but then the code calls reconcile AGAIN at line 797.
# The fix: use ONLY _map_tool_call result, remove the duplicate reconcile call.

OLD1 = """            available_tools = set(runtime.list_tools())
            try:
                mapped_tool_name, payload, repair_meta = reconcile_and_repair_tool_call(tool_name, payload, available_tools, agent_id)
            except (TypeError, ValueError):
                mapped_tool_name, payload = reconcile_and_repair_tool_call(tool_name, payload, available_tools)
                repair_meta = {
                    "requested_tool": tool_name,
                    "resolved_tool": mapped_tool_name,
                    "repaired": mapped_tool_name != tool_name,
                    "repair_mode": False,
                }"""

NEW1 = """            available_tools = set(runtime.list_tools())
            mapped_tool_name, payload = _map_tool_call(tool_name, payload, available_tools, agent_id)
            repair_meta = {
                "requested_tool": tool_name,
                "resolved_tool": mapped_tool_name,
                "repaired": mapped_tool_name != tool_name,
                "repair_mode": False,
            }"""

if OLD1 not in src:
    # try without the ValueError variant
    OLD1b = """            available_tools = set(runtime.list_tools())
            try:
                mapped_tool_name, payload, repair_meta = reconcile_and_repair_tool_call(tool_name, payload, available_tools, agent_id)
            except TypeError:
                mapped_tool_name, payload = reconcile_and_repair_tool_call(tool_name, payload, available_tools)
                repair_meta = {
                    "requested_tool": tool_name,
                    "resolved_tool": mapped_tool_name,
                    "repaired": mapped_tool_name != tool_name,
                    "repair_mode": False,
                }"""
    if OLD1b in src:
        src = src.replace(OLD1b, NEW1, 1)
        print("Fix 1 applied: removed duplicate reconcile, use _map_tool_call directly")
    else:
        print("ERROR: reconcile anchor not found in either variant")
        sys.exit(1)
else:
    src = src.replace(OLD1, NEW1, 1)
    print("Fix 1 applied: removed duplicate reconcile, use _map_tool_call directly")

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
    print("Done - run pytest then restart backend")
