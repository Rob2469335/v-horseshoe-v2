import json
import subprocess
import random
import hashlib
import os

from transformers import AutoTokenizer
_TOKENIZER = AutoTokenizer.from_pretrained(
    r"C:\Users\rober\models\Qwen3.5-4B-Base-HF", local_files_only=True
)

repo_dir = r"C:\Users\rober\Projects\v-horseshoe-v2"
train_path = r"C:\Users\rober\Projects\qwen_train_data\real_25_dataset_v1.jsonl"
exam_path = r"C:\Users\rober\Projects\qwen_train_data\exam\blind_hidden_exam_input.jsonl"
out_train = r"C:\Users\rober\Projects\qwen_train_data\v4_train_inputs.jsonl"
out_exam = r"C:\Users\rober\Projects\qwen_train_data\v4_exam_inputs.jsonl"

def get_prefix_code(commit):
    try:
        files_str = subprocess.check_output(
            f"git diff-tree --no-commit-id --name-only -r {commit}",
            shell=True, cwd=repo_dir
        ).decode('utf-8', errors='ignore')
    except subprocess.CalledProcessError:
        return ""

    files = [f for f in files_str.strip().split("\n") if f and not f.startswith("test") and "test_" not in f]

    file_changes = []
    for file in files:
        if file.endswith((".md", ".json", ".lock")): continue  # Exclude docs/data files
        try:
            diff = subprocess.check_output(f"git diff -U0 {commit}~1 {commit} -- {file}", shell=True, cwd=repo_dir).decode('utf-8', errors='ignore')
            changed_lines = []
            for line in diff.split("\n"):
                if line.startswith("@@"):
                    parts = line.split(" ")
                    pre_info = parts[1]
                    start_line = int(pre_info.split(",")[0][1:]) if "," in pre_info else int(pre_info[1:])
                    count = int(pre_info.split(",")[1]) if "," in pre_info else 1
                    if count == 0: count = 1
                    changed_lines.extend(range(start_line, start_line + count))
            if changed_lines:
                file_changes.append({
                    "file": file,
                    "min": min(changed_lines),
                    "max": max(changed_lines),
                    "span": max(changed_lines) - min(changed_lines) + 1
                })
        except:
            continue

    file_changes.sort(key=lambda x: x["span"], reverse=True)

    code_blocks = []
    BUDGET_LINES = 300
    # NB: budget code context by TOKENS (via the real tokenizer), not chars.
    # Python source tokenizes ~3.0-3.3 chars/token, so an 8000-char cap is ~2400+
    # tokens and blows the training max_seq_length. Measured against Ceil A770:
    # 2048 fits, 2528 fits, 2576+ OOM; full-row target ~2200 keeps headroom.
    # 800 code tokens + worst-overhead (prompt 152 + trace 616 + answer 877 = 1645)
    # => worst assembled row ~2445, safely under the 2528 ceiling.
    MAX_TOKENS_CODE = os.environ.get("V4_CODE_TOKEN_BUDGET") or 800
    tok_used = 0

    def _tokens(text):
        return len(_TOKENIZER.encode(text, add_special_tokens=False))

    for fc in file_changes:
        if BUDGET_LINES <= 20 or tok_used >= int(MAX_TOKENS_CODE): break
        file = fc["file"]
        min_line = fc["min"]
        max_line = fc["max"]

        try:
            content = subprocess.check_output(f"git show {commit}~1:{file}", shell=True, cwd=repo_dir, stderr=subprocess.STDOUT).decode('utf-8', errors='ignore')
            lines = content.split('\n')
            total_lines = len(lines)

            window_size = min(BUDGET_LINES, total_lines)

            if total_lines <= window_size:
                block_parts = "\n".join(lines)
                BUDGET_LINES -= total_lines
            else:
                seed = int(hashlib.md5((commit + file).encode()).hexdigest(), 16)
                random.seed(seed)

                span = fc["span"]
                if span >= window_size:
                    slice_start = min_line
                else:
                    slack = window_size - span
                    offset = random.randint(0, slack)
                    slice_start = max(1, min_line - offset)

                slice_end = slice_start + window_size - 1
                if slice_end > total_lines:
                    slice_end = total_lines
                    slice_start = max(1, slice_end - window_size + 1)

                sliced_lines = lines[slice_start-1:slice_end]
                block_parts = f"(Lines {slice_start}-{slice_end}) ---\n" + "\n".join(sliced_lines)
                BUDGET_LINES -= window_size

            # Token-budget enforcement: if this block would push the code context
            # over the token cap, trim its lines to the remaining allowance.
            # The reminder suffix is NOT appended past the budget (the old
            # char-slice path leaked ~35 chars over on every truncated file).
            block = f"--- FILE: {file} ---\n" + block_parts
            block_toks = _tokens(block)
            if tok_used + block_toks > int(MAX_TOKENS_CODE):
                remaining = max(1, int(MAX_TOKENS_CODE) - tok_used)
                keep_lines = len(lines)
                while keep_lines > 1 and _tokens(f"--- FILE: {file} ---\n" + "\n".join(lines[:keep_lines])) > remaining:
                    keep_lines -= 1
                block = f"--- FILE: {file} ---\n" + "\n".join(lines[:keep_lines])
                block_toks = _tokens(block)

            if block_toks <= 0:
                continue
            code_blocks.append(block)
            tok_used += block_toks
            if tok_used >= int(MAX_TOKENS_CODE):
                break

        except Exception as e:
            continue

    return "\n\n".join(code_blocks)

def process_dataset(in_path, out_path, is_exam=False):
    out_items = []
    with open(in_path, 'r', encoding='utf-8') as f:
        for line in f:
            item = json.loads(line)
            commit = item["exam_id"].replace("real_exam_", "") if is_exam else item.get("source_commit", "")
            if not commit: continue

            code_context = get_prefix_code(commit)

            if is_exam:
                task_evidence = item["input_prompt"]
                item["input_prompt"] = f"{task_evidence}\n\nCODE CONTEXT (Pre-fix state):\n{code_context}"
            else:
                raw = item.get("raw_extracted", "")
                task = ""
                evidence = ""
                curr = None
                curr_lines = []
                for r_line in raw.split("\n"):
                    if r_line.startswith("TASK:"):
                        curr = "TASK"
                        curr_lines = [r_line.replace("TASK:", "").strip()]
                    elif r_line.startswith("EVIDENCE:"):
                        if curr == "TASK": task = "\n".join(curr_lines).strip()
                        curr = "EVIDENCE"
                        curr_lines = [r_line.replace("EVIDENCE:", "").strip()]
                    elif r_line.startswith("FILES:") or r_line.startswith("DIAGNOSIS:"):
                        if curr == "EVIDENCE": evidence = "\n".join(curr_lines).strip()
                        curr = None
                    elif curr:
                        curr_lines.append(r_line)

                if curr == "EVIDENCE": evidence = "\n".join(curr_lines).strip()
                elif curr == "TASK": task = "\n".join(curr_lines).strip()

                prompt = ""
                if task or evidence:
                    if task: prompt += f"TASK:\n{task}\n\n"
                    if evidence: prompt += f"EVIDENCE:\n{evidence}\n\n"
                else:
                    parts = raw.split("FILES:")
                    prompt += parts[0].strip() + "\n\n"

                item["v4_prompt"] = f"{prompt}CODE CONTEXT (Pre-fix state):\n{code_context}"

            out_items.append(item)

    with open(out_path, 'w', encoding='utf-8') as f:
        for it in out_items:
            f.write(json.dumps(it) + "\n")
    print(f"Processed {len(out_items)} items for {out_path}")

process_dataset(train_path, out_train, False)
process_dataset(exam_path, out_exam, True)