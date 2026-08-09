from __future__ import annotations

import logging
import re
from pathlib import Path

from swarm_os.services.legal.transcript_search import TranscriptIndex

log = logging.getLogger(__name__)


def build_analysis(indices: list[TranscriptIndex], outfile: str) -> str:
    """Builds a factual, page-grounded analysis of trial transcripts.
    Pure and synchronous; outputs markdown.
    """
    out = []
    out.append("# Trial Transcript Analysis")
    out.append("This report is derived entirely from the transcript text. "
               "It reports WHAT is on a page and WHERE. It does not assert legal significance.\n")

    for idx_num, idx in enumerate(indices, 1):
        day_label = f"Day {idx_num}"
        if idx.source:
            day_label += f" ({Path(idx.source).name})"
        
        out.append(f"## {day_label} - Chronology\n")
        
        if not idx.passages:
            out.append("No passages found for this day.\n")
            continue

        chronology = _build_chronology(idx)
        for c in chronology:
            out.append(c)
        out.append("")

        out.append(f"## {day_label} - Witness Matrix\n")
        matrix = _build_witness_matrix(idx)
        if not matrix:
            out.append("No witnesses found.\n")
        else:
            out.append("| Witness | Pages | Examining Attorneys | Testimony Summary |")
            out.append("|---|---|---|---|")
            for row in matrix:
                out.append(f"| {row['name']} | {row['pages']} | {row['attorneys']} | {row['summary']} |")
        out.append("")

        out.append(f"## {day_label} - Objections & Rulings Log\n")
        objections = _build_objections_log(idx)
        if not objections:
            out.append("No objections found.\n")
        else:
            for obj in objections:
                out.append(obj)
        out.append("")

        out.append(f"## {day_label} - Batson Challenge Pass\n")
        batson = _build_batson_pass(idx)
        out.append(batson)
        out.append("")

    out_path = Path(outfile)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(out), encoding="utf-8")
    return str(out_path)


def _build_chronology(idx: TranscriptIndex) -> list[str]:
    events = []
    current_exam_atty = None
    last_witness_page = None
    
    ruling_re = re.compile(r"\b(OVERRULED|SUSTAINED|ALLOW IT|YOU CAN ANSWER|DENIED|GRANTED|STRIKE THAT)\b")
    
    for i, p in enumerate(idx.passages):
        t_upper = p.text.upper()
        
        if p.speaker == "THE WITNESS":
            last_witness_page = p.page
            examiner = None
            for j in range(i-1, max(-1, i-10), -1):
                prev_p = idx.passages[j]
                if prev_p.speaker not in ("THE WITNESS", "THE COURT"):
                    examiner = prev_p.speaker
                    break
                elif prev_p.speaker == "THE WITNESS":
                    break
            
            if examiner and current_exam_atty != examiner:
                if current_exam_atty is not None:
                    events.append(f"- Examination by {current_exam_atty} ends (Page {last_witness_page})")
                current_exam_atty = examiner
                events.append(f"- Examination by {current_exam_atty} begins (Page {p.page})")
                
        if "OBJECTION" in t_upper and p.speaker not in ("THE COURT", "THE WITNESS"):
            events.append(f"- Objection by {p.speaker} (Page {p.page})")
            
        if p.speaker == "THE COURT":
            for r_match in ruling_re.finditer(t_upper):
                r = r_match.group(1).title()
                if r in ("Allow It", "You Can Answer"): 
                    r = "Overruled (Allowed)"
                elif r == "Strike That": 
                    r = "Sustained (Stricken)"
                events.append(f"- Ruling: {r} by THE COURT (Page {p.page})")
                
            if "CHARGE THE JURY" in t_upper or "CHARGE TO THE JURY" in t_upper or "INSTRUCT THE JURY" in t_upper:
                events.append(f"- Jury Instructions / Charge (Page {p.page})")
                
        if "SIDEBAR" in t_upper and p.speaker not in ("THE WITNESS",):
            events.append(f"- Sidebar mentioned by {p.speaker} (Page {p.page})")
            
        if ("ADJOURN" in t_upper or "RECESS" in t_upper) and p.speaker not in ("THE WITNESS",):
            events.append(f"- Adjournment/Recess mentioned by {p.speaker} (Page {p.page})")

    if current_exam_atty and last_witness_page:
        events.append(f"- Examination by {current_exam_atty} ends (Page {last_witness_page})")
        
    res = []
    for e in events:
        if not res or res[-1] != e:
            res.append(e)
    return res


def _build_witness_matrix(idx: TranscriptIndex) -> list[dict[str, str]]:
    witnesses = []
    in_witness = False
    pages: set[int] = set()
    attorneys: set[str] = set()
    answers: list[str] = []
    
    def flush_witness():
        if not pages: 
            return
        answers.sort(key=len, reverse=True)
        best_answers = answers[:3]
        summary_snippets = []
        for ans in best_answers:
            txt = ans.replace('\n', ' ')
            summary_snippets.append(txt[:150] + ("..." if len(txt) > 150 else ""))
            
        p_min = min(pages)
        witnesses.append({
            "name": "Unnamed Witness",
            "pages": f"{p_min}-{max(pages)}",
            "attorneys": ", ".join(sorted(attorneys)),
            "summary": f"Testified regarding: {' | '.join(summary_snippets)} [not assessed; see page {p_min}]" if summary_snippets else f"No substantive testimony extracted [not assessed; see page {p_min}]"
        })
    
    last_witness_idx = -1
    for i, p in enumerate(idx.passages):
        if p.speaker == "THE WITNESS":
            if not in_witness:
                if last_witness_idx != -1 and i - last_witness_idx > 50:
                    flush_witness()
                    pages.clear()
                    attorneys.clear()
                    answers.clear()
                in_witness = True
                
            last_witness_idx = i
            pages.add(p.page)
            for j in range(i-1, max(-1, i-10), -1):
                prev = idx.passages[j]
                if prev.speaker not in ("THE WITNESS", "THE COURT"):
                    attorneys.add(prev.speaker)
                    break
            
            if len(p.text.split()) > 4:
                answers.append(p.text)
        else:
            if in_witness and i - last_witness_idx > 50:
                in_witness = False
                
    flush_witness()
    return witnesses


def _build_objections_log(idx: TranscriptIndex) -> list[str]:
    logs = []
    pending_objections = []
    ruling_re = re.compile(r"\b(OVERRULED|SUSTAINED|ALLOW IT|YOU CAN ANSWER|DENIED|GRANTED|STRIKE THAT)\b")
    
    for i, p in enumerate(idx.passages):
        t_upper = p.text.upper()
        
        if "OBJECTION" in t_upper and p.speaker not in ("THE COURT", "THE WITNESS"):
            pending_objections.append((p.speaker, p.page, p.text))
            
        if p.speaker == "THE COURT":
            rulings = []
            for r_match in ruling_re.finditer(t_upper):
                r = r_match.group(1).title()
                if r in ("Allow It", "You Can Answer"):
                    r = "Overruled (Allowed)"
                elif r == "Strike That":
                    r = "Sustained (Stricken)"
                rulings.append(r)
                
            if rulings and pending_objections:
                r_combined = " / ".join(rulings)
                for obj_speaker, obj_page, obj_text in pending_objections:
                    txt = obj_text.replace('\n', ' ')
                    logs.append(f"- **Objection** by {obj_speaker} on Page {obj_page}: \"{txt}\" -> **Ruling**: {r_combined} (Page {p.page}) [not assessed; see page {p.page}]")
                pending_objections.clear()
                    
    for obj_speaker, obj_page, obj_text in pending_objections:
        txt = obj_text.replace('\n', ' ')
        logs.append(f"- **Objection** by {obj_speaker} on Page {obj_page}: \"{txt}\" -> **Ruling**: No explicit ruling found nearby [not assessed; see page {obj_page}]")
        
    return logs


def _build_batson_pass(idx: TranscriptIndex) -> str:
    # Batson doctrine mentions: "the Batson challenge", "peremptory strikes",
    # "pattern of discrimination". A person named "Mr. Batson" is NOT a
    # challenge — require a doctrine noun after "batson", or the peremptory/
    # pattern phrases. THE COURT is included (the Court acknowledging a real
    # challenge is the most on-point passage); THE WITNESS is excluded (a
    # witness never raises a challenge).
    batson_passages = []
    batson_re = re.compile(
        r"\bbatson\s+(?:challenge|motion|objection|claim|argument|violation|error|issue)\b"
        r"|\bperemptory\b|\bpattern of discrimination\b",
        re.IGNORECASE,
    )

    for p in idx.passages:
        if p.speaker == "THE WITNESS":
            continue

        if batson_re.search(p.text):
            batson_passages.append(p)

    if not batson_passages:
        return "No Batson challenge detected.\n"

    out = []
    out.append("The passages below mention the Batson doctrine, peremptory strikes, or a")
    out.append("pattern of discrimination during jury selection:\n")

    for p in batson_passages:
        txt = p.text.replace('\n', ' ')
        out.append(f"- **{p.speaker}** (Page {p.page}): \"{txt}\"")

    out.append("\n**Plain-Language Summary**: The passages above reference jury-selection")
    out.append("strikes or the Batson doctrine. Whether a viable challenge was made, preserved,")
    out.append(f"or ruled upon is a question for a qualified person, not this tool. [not assessed; see page {batson_passages[0].page}]")

    return "\n".join(out)
