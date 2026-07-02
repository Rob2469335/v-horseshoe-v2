import os
with open('runtime_v2/services/fallback_manager.py', 'r') as f:
    content = f.read()
content = content.replace('models.append({', 'if \'canopylabs\' in m[\'id\']: continue
                models.append({')
with open('runtime_v2/services/fallback_manager.py', 'w') as f:
    f.write(content)
