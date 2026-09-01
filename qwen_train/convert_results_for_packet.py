"""Shim: convert fresh budget-600 full-exam results into the schema/paths
that build_blind_packet.py expects, WITHOUT editing the protocol scripts.

build_blind_packet.py reads:
  results/exam_adapter_results.jsonl  (expects content, content_length_chars, finish_reason, completion_tokens, error)
  results/exam_base_only_results.jsonl

Our fresh full_exam.py output uses: content_full, content_len, reasoning_len, etc.
This converts in place. Protocol scripts stay untouched.
"""
import json
from pathlib import Path

RESULTS = Path("C:/Users/rober/Projects/v-horseshoe-v2/qwen_train/results")
SRC = {
    "exam_adapter_results.jsonl": RESULTS / "adapter_budget600_full2.jsonl",
    "exam_base_only_results.jsonl": RESULTS / "base_budget600_full.jsonl",
}


def convert(src: Path, dst: Path):
    rows = []
    for line in src.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        content = r.get("content_full") or ""
        new = {
            "exam_id": r["exam_id"],
            "input_prompt_preview": r.get("input_prompt_preview", ""),
            "content": content,
            "reasoning": r.get("reasoning_full", ""),
            "finish_reason": r.get("finish_reason"),
            "content_length_chars": len(content),
            "reasoning_length_chars": r.get("reasoning_len", 0),
            "prompt_tokens": r.get("prompt_tokens", 0),
            "completion_tokens": r.get("completion_tokens", 0),
            "generation_time_s": r.get("elapsed_s", 0),
            "tag": r.get("tag", ""),
            "error": r.get("error"),
        }
        rows.append(new)
    dst.write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + "\n",
        encoding="utf-8")
    print(f"wrote {dst.name}: {len(rows)} rows")
    # report content summary to confirm the schema load will be sane
    for r in rows:
        print(f"  {r['exam_id']}: finish={r['finish_reason']} content={r['content_length_chars']}ch error={r['error']}")


for dst_name, src_path in SRC.items():
    # BACK UP any prior protocol-shaped file first (rollback protection)
    dst = RESULTS / dst_name
    if dst.exists():
        bak = RESULTS / (dst_name + ".bak_protocol")
        if not bak.exists():
            bak.write_text(dst.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"backed up existing {dst_name} -> {dst_name}.bak_protocol")
    convert(src_path, dst)