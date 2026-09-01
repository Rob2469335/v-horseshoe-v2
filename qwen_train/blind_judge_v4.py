"""Blind judge: scores blind_packet_v4.jsonl (forward) and blind_packet_v4_swapped.jsonl
into judge_scores_v4.jsonl using the go deepseek-v4-flash model.

Strictly blind: only reads the packet files (never blind_key_v4.json). Rubric is
fixed and printed. Each item scored twice (forward + swapped), temp 0.
"""
import json
import os
import re
import time
import httpx
from pathlib import Path

RESULTS = Path("C:/Users/rober/Projects/v-horseshoe-v2/qwen_train/results")
PACKET = RESULTS / "blind_packet_v4.jsonl"
SWAPPED = RESULTS / "blind_packet_v4_swapped.jsonl"
OUT = RESULTS / "judge_scores_v4.jsonl"
BASE = os.environ.get("JUDGE_BASE", "https://opencode.ai/zen/go/v1")
KEY = os.environ["OPENAI_API_KEY"]
MODEL = "deepseek-v4-flash"

RUBRIC = """You are a senior code-reviewer evaluating which of two AI-generated code-debugging ANSWERS is better for a given TASK (diagnosing + planning a fix for a real repo bug). Judge ONLY answer quality:

- ACCURACY/DENSITY: does the answer correctly localize the real bug and name real files/functions? Is it precise and information-dense (not padded filler)?
- COMPLETENESS: does it cover DIAGNOSIS, PLAN, and VALIDATION concretely?
- STRUCTURE/CLARITY: clear DIAGNOSIS / PLAN / VALIDATION sections, usable by an engineer.
- Grounding over embellishment: specific, grounded statements beat vague elaboration.
- CORRECTNESS OVER VOLUME: a wrong or fabricated diagnosis is worth LESS than nothing, no matter how long or well-organized it is. Length is NOT a signal of quality.

WORKED COUNTER-EXAMPLE (a real failure to avoid): a 5000-char answer that confidently invents a regex-filter theory and builds a whole implementation around it should LOSE to a 1400-char answer that names the actual function (webpage_snapshot) and its real mechanism (html.unescape). The longer answer was fabricated; the shorter one was correct. Judge substance, never volume.

The two answers below have been made the same length by truncation, so length differences are GONE by construction — judge ONLY content.

Reply with EXACTLY ONE LINE: the letter "A", "B", or "TIE", then a short reason on the same line separated by a pipe. Example: "A|more precise localization". Nothing else."""


def _equalize_length(a: str, b: str) -> tuple[str, str]:
    """Truncate both answers to the shorter one's length so the judge cannot
    perceive a length difference. This is the structural fix for the
    length-over-correctness bias caught on bedddcf (judge preferred a 5009ch
    fabricated answer over the correct 1400ch one)."""
    n = min(len(a), len(b))
    return a[:n], b[:n]


def judge_answers(client: httpx.Client, task: str, a: str, b: str) -> str:
    a2, b2 = _equalize_length(a, b)
    prompt = (
        f"TASK: {task}\n\n"
        f"ANSWER A:\n{a2}\n\n"
        f"ANSWER B:\n{b2}\n\n"
        f"(Note: A and B are equal length by truncation. Judge ONLY content.)\n"
        f"Which answer is better? Reply per rubric."
    )
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": RUBRIC},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 40,
        "temperature": 0.0,
    }
    headers = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
    last_err = None
    for attempt in range(5):
        try:
            r = client.post(
                f"{BASE}/chat/completions", json=payload, headers=headers, timeout=120.0)
            r.raise_for_status()
            c = (r.json()["choices"][0]["message"]["content"] or "").strip()
            m = re.match(r"\s*([AB])", c, re.IGNORECASE)
            if m:
                return m.group(1).upper()
            # also accept a single letter token anywhere early
            m2 = re.search(r"^\s*[AB]\b", c)
            if m2:
                return m2.group(0).strip().upper()
            return "TIE"
        except Exception as e:
            last_err = e
            print(f"  judge attempt {attempt+1}/5 failed: {e}", flush=True)
            time.sleep(3)
    # FAIL LOUDLY: a judge call that cannot complete must NOT become a TIE —
    # a run full of default-TIEs would look like a real (boring) result.
    raise RuntimeError(f"judge call failed after 5 attempts: {last_err}")


def main():
    def load(p):
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]

    fwd = load(PACKET)
    swp = load(SWAPPED)
    print(f"packet rows: forward={len(fwd)} swapped={len(swp)}")
    print(f"judge model: {MODEL} via {BASE}\n")

    client = httpx.Client(timeout=120.0)
    rows = []
    for p_f, p_s in zip(fwd, swp):
        eid = p_f["exam_id"]
        assert p_s["exam_id"] == eid, "swap order mismatch"
        print(f"--- {eid} ---", flush=True)
        v_fwd = judge_answers(client, p_f["task"], p_f["answer_A"], p_f["answer_B"])
        print(f"  forward:  {v_fwd}", flush=True)
        time.sleep(1)
        v_swp = judge_answers(client, p_s["task"], p_s["answer_A"], p_s["answer_B"])
        print(f"  swapped:  {v_swp}", flush=True)
        time.sleep(1)
        rows.append({"exam_id": eid, "forward": v_fwd, "swapped": v_swp,
                     "notes": "blind judge, temp0"})

    client.close()
    with open(OUT, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    print(f"\nwrote {OUT}: {len(rows)} scored items")


if __name__ == "__main__":
    main()