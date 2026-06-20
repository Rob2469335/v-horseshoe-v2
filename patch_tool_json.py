import re
path = r'C:\Users\rober\Projects\v-horseshoe-v2\swarm_os\services\agent_service.py'
with open(path, 'r', encoding='utf-8') as f:
    src = f.read()

old = """            try:
                payload = parse_and_repair_json(raw_payload_str)
            except Exception as exc:
                logger.error(f"[TOOL CALL FAILURE] Model '{active_model}' generated unrepairable tool payload JSON: {exc}")"""

new = """            try:
                # Sanitize invalid escape sequences before parsing (common in NVIDIA/cloud model outputs)
                raw_payload_str = re.sub(r'(?<!\\\\)\\\\(?!["\\\\/bfnrtu0-9])', r'\\\\\\\\', raw_payload_str)
                payload = parse_and_repair_json(raw_payload_str)
            except Exception as exc:
                logger.error(f"[TOOL CALL FAILURE] Model '{active_model}' generated unrepairable tool payload JSON: {exc}")"""

if old in src:
    src = src.replace(old, new)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(src)
    print('Patched successfully')
else:
    print('ERROR: not found - printing actual lines')
    for i, line in enumerate(src.splitlines()[576:588], start=577):
        print(f'{i}: {repr(line)}')
