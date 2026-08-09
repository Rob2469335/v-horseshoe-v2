from __future__ import annotations

import logging
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
    exam_start_page = None
    
    for i in range(len(idx.passages)):
        p1 = idx.passages[i]
        p2 = idx.passages[i+1] if i + 1 < len(idx.passages) else None
        t_upper = p1.text.upper()
        
        # Detect examination by tracking attorney questions followed by witness answers.
        # This bypasses the need for the explicitly parsed 'CROSS-EXAMINATION' headers
        # which transcript_search strips as layout.
        if p2 and p2.speaker == "THE WITNESS" and p1.speaker not in ("THE COURT", "THE WITNESS"):
            if current_exam_atty != p1.speaker:
                if current_exam_atty is not None:
                    events.append(f"- Examination by {current_exam_atty} ends (Page {p1.page})")
                current_exam_atty = p1.speaker
                exam_start_page = p1.page
                events.append(f"- Examination by {current_exam_atty} begins (Page {exam_start_page})")

        if "OBJECTION" in t_upper:
            events.append(f"- Objection by {p1.speaker} (Page {p1.page})")
        if "OVERRULED" in t_upper or "SUSTAINED" in t_upper:
            r = "Overruled" if "OVERRULED" in t_upper else "Sustained"
            events.append(f"- Ruling: {r} by {p1.speaker} (Page {p1.page})")
        if "SIDEBAR" in t_upper:
            events.append(f"- Sidebar mentioned by {p1.speaker} (Page {p1.page})")
        if "JURY INSTRUCTION" in t_upper or "CHARGE" in t_upper:
            if p1.speaker == "THE COURT":
                events.append(f"- Jury Instructions / Charge (Page {p1.page})")
        if "ADJOURN" in t_upper or "RECESS" in t_upper:
            events.append(f"- Adjournment/Recess mentioned by {p1.speaker} (Page {p1.page})")

    # Flush dangling examination
    if current_exam_atty and idx.passages:
        events.append(f"- Examination by {current_exam_atty} ends (Page {idx.passages[-1].page})")
        
    # Deduplicate events that are logged multiple times for the same logical occurrence
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
    summary_snippets: list[str] = []
    
    for i, p in enumerate(idx.passages):
        if p.speaker == "THE WITNESS":
            in_witness = True
            pages.add(p.page)
            if i > 0 and idx.passages[i-1].speaker not in ("THE WITNESS", "THE COURT"):
                attorneys.add(idx.passages[i-1].speaker)
            if len(summary_snippets) < 3 and len(p.text.split()) > 5:
                # Basic string truncation for neutrality, avoiding LLM analysis.
                txt = p.text.replace('\n', ' ')
                summary_snippets.append(txt[:100] + ("..." if len(txt) > 100 else ""))
        else:
            if in_witness and p.speaker not in ("THE WITNESS", "THE COURT") and (i+1 < len(idx.passages) and idx.passages[i+1].speaker != "THE WITNESS"):
                p_min = min(pages) if pages else 0
                witnesses.append({
                    "name": "Unnamed Witness",
                    "pages": f"{p_min}-{max(pages)}" if pages else "",
                    "attorneys": ", ".join(sorted(attorneys)),
                    "summary": f"Testified regarding: {' | '.join(summary_snippets)} [not assessed; see page {p_min}]"
                })
                in_witness = False
                pages = set()
                attorneys = set()
                summary_snippets = []
                
    if in_witness:
        p_min = min(pages) if pages else 0
        witnesses.append({
            "name": "Unnamed Witness",
            "pages": f"{p_min}-{max(pages)}" if pages else "",
            "attorneys": ", ".join(sorted(attorneys)),
            "summary": f"Testified regarding: {' | '.join(summary_snippets)} [not assessed; see page {p_min}]"
        })
        
    return witnesses


def _build_objections_log(idx: TranscriptIndex) -> list[str]:
    logs = []
    for i, p in enumerate(idx.passages):
        t_upper = p.text.upper()
        if "OBJECTION" in t_upper:
            ruling = "No explicit ruling found nearby"
            # Look ahead a few passages for the court's ruling
            for j in range(i+1, min(i+6, len(idx.passages))):
                fp = idx.passages[j]
                if fp.speaker == "THE COURT":
                    if "OVERRULED" in fp.text.upper():
                        ruling = f"Overruled (Page {fp.page})"
                        break
                    elif "SUSTAINED" in fp.text.upper():
                        ruling = f"Sustained (Page {fp.page})"
                        break
            
            txt = p.text.replace('\n', ' ')
            logs.append(f"- **Objection** by {p.speaker} on Page {p.page}: \"{txt}\" -> **Ruling**: {ruling} [not assessed; see page {p.page}]")
    return logs


def _build_batson_pass(idx: TranscriptIndex) -> str:
    batson_passages = []
    for p in idx.passages:
        txt = p.text.lower()
        if "batson" in txt or "peremptory" in txt or "pattern of discrimination" in txt:
            batson_passages.append(p)
            
    if not batson_passages:
        return "No Batson challenge detected.\n"
        
    out = []
    out.append("A Batson challenge involves an objection to the use of peremptory strikes on the basis of race or other protected classes.")
    out.append("The transcript contains the following relevant argument:\n")
    
    for p in batson_passages:
        txt = p.text.replace('\n', ' ')
        out.append(f"- **{p.speaker}** (Page {p.page}): \"{txt}\"")
        
    out.append("\n**Plain-Language Summary**: The passages above show an argument regarding jury selection strikes.")
    out.append(f"Whether the challenge was viable, preserved, or timely is a question for a qualified person, not this tool. [not assessed; see page {batson_passages[0].page}]")
    
    return "\n".join(out)
