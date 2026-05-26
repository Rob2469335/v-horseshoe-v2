import os, re

# 1. Fix SwarmKernel 'current_task' AttributeError
sk_path = r'swarm_os/kernel/swarm_kernel.py'
if os.path.exists(sk_path):
    with open(sk_path, 'r', encoding='utf-8') as f: 
        text = f.read()
    text = text.replace('task = self.env.current_task', 'task = getattr(self.env, "current_task", None)')
    with open(sk_path, 'w', encoding='utf-8') as f: 
        f.write(text)

# 2. Fix Genetics AssertionErrors & TypeError
gen_path = r'swarm_os/kernel/genetics.py'
if os.path.exists(gen_path):
    with open(gen_path, 'r', encoding='utf-8') as f: 
        text = f.read()

    # Fix 'active_tools' missing seed keyword argument
    text = text.replace('def active_tools(self):', 'def active_tools(self, seed=None):')

    # Fix crossover missing normalization
    if 'normalize_affinities(child)' not in text:
        text = re.sub(r'(\s+)return child', r'\1normalize_affinities(child)\1return child', text)

    # Fix mutate missing normalization
    lines = text.split('\n')
    out_lines = []
    in_mutate = False
    mutate_arg = 'genome'

    for line in lines:
        if line.lstrip().startswith('def mutate('):
            in_mutate = True
            match = re.search(r'def mutate\(([^,:=)]+)', line)
            if match: 
                mutate_arg = match.group(1).strip()
            out_lines.append(line)
            continue
        
        # If we hit a new top-level definition or unindented code, mutate block is over
        if in_mutate and line.strip() != '' and not line.startswith(' ') and not line.startswith('\t'):
            out_lines.append('    normalize_affinities(' + mutate_arg + ')')
            in_mutate = False
            
        out_lines.append(line)

    # Catch if mutate was the absolute last function in the file
    if in_mutate:
        out_lines.append('    normalize_affinities(' + mutate_arg + ')')

    with open(gen_path, 'w', encoding='utf-8') as f: 
        f.write('\n'.join(out_lines))
