"""Transcript search for Rob's Lawyer — ingest a trial transcript text file and
extract speaker passages with page numbers, offline.

The use case that drives this: "show me only the pages where attorney X spoke."
Federal trial transcripts are party-restricted; this module is a local, offline
search over whatever transcript text the user lawfully obtained (court-reporter
copy, PACER party download). No network, no Qdrant — pure text parsing.

Handled format (SDNY court-reporter transcripts, e.g. the J591dun1 series):

- Pages split on the reporter footer ("SOUTHERN DISTRICT REPORTERS, P.C.").
- The page number is the first standalone integer line after each footer.
- Speakers: "THE COURT:", "MR. X:", "MS. X:", "THE WITNESS:", "THE REPORTER:".
- Examination blocks opened by a "BY MR./MS. X:" header — the following "Q."
  lines belong to that attorney, "A." lines to the witness. Without tracking
  this, an attorney's actual cross-examination (all the Q. lines) would be
  invisible to a naive "MS. X:" text search.
- Every content line carries a leading line number (1-25) that is stripped.

KNOWN LIMITATION (hand-verified 2026-08-09, real SDNY pages 506/519/620/655):
A bare, colon-less line (e.g. the Court calling "Ms. Al-Shabazz.") that follows a
colon-prefixed line of the SAME speaker is treated as a continuation and merged
into that speaker's passage. This is benign when the two halves are the same
speaker (verified on p.506: "All right. Thank you. Ms. Al-Shabazz." stays the
Court's), and the dangerous shapes — an objection splitting a question, a
multi-attorney sidebar, a judge interjecting mid-examination — were all
hand-verified correct on real pages (p.519 objection-mid-question, p.620/p.655
sidebars, p.7 Norwood-direct objections). The gap is narrow and known; if this
tool is ever wired into an endpoint where output is consumed WITHOUT
cross-referencing the source page, re-audit the attribution before trusting it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_FOOTER_RE = re.compile(r"SOUTHERN DISTRICT REPORTERS, P\.C\.[^\n]*(?:\n[^\n]*){0,2}")
# A speaker attribution prefix at the start of a line, e.g. "THE COURT:",
# "MS. AL-SHABAZZ:", "MR. DINNERSTEIN:" (uppercase words/dots/spaces + colon;
# hyphens allowed for hyphenated names like Al-Shabazz).
_NAME_CHARS = r"A-Z0-9.,' -"
_SPEAKER_RE = re.compile(rf"^([A-Z][{_NAME_CHARS}]*?):\s+(.*)$")
# "BY MS. AL-SHABAZZ:" examination-block header.
_BY_HEADER_RE = re.compile(rf"^BY\s+([A-Z][{_NAME_CHARS}]*?):\s*$")
_Q_RE = re.compile(r"^Q\.\s+(.*)$")
_A_RE = re.compile(r"^A\.\s+(.*)$")
_LINENO_RE = re.compile(r"^\s*\d{1,4}\s{2,}(.*)$")
# A standalone printed page number. Mid-trial pages are 3-4 digits; early
# transcript pages can be 1-2 digits. The parser locates it by position — the
# standalone integer immediately followed by a volume-id line ("J5ATDUN1",
# "J591dun1") at the start of a footer-split block — so a leading line-number
# column (1-25, never followed by a volume id) is NOT misread as a page number.
_PAGE_NO_RE = re.compile(r"^\s*(\d{1,4})\s*$")
# Page header line: volume-id token + whitespace + short section label, e.g.
# "J5ATDUN1  Mueller - Direct", "J58TDUN1  Dewitt", "J571dun1 - Corrected".
# These are layout printed at the top of each page — NOT speech. They leak into
# a passage only when a page break splits an utterance, so they must be dropped.
_VOLUME_ID_RE = re.compile(r"^[Jj]\d+[A-Za-z]+\d+[A-Za-z]*\d*(?:Page)?$")
_PAGE_HEADER_RE = re.compile(r"^[Jj]\d+[A-Za-z]+\d+[A-Za-z]*\d*(?:Page)?\s+\S.{0,40}$")
_STAGE_RE = re.compile(r"^\((?:Jury|Witness|Recess|Continued|At sidebar|In open court|In open c)[^)]*\)$", re.I)
# Examination section headers that are layout, not speech ("CROSS-EXAMINATION",
# "DIRECT EXAMINATION", "REDIRECT EXAMINATION", "RECROSS EXAMINATION").
_SECTION_HEADER_RE = re.compile(
    r"^(?:CROSS|DIRECT|REDIRECT|RECROSS|VOIR DIRE)?[-\s]*(?:EXAMINATION|QUESTIONS BY)\b", re.I
)
# A bare colon-less line that looks like a speaker reference, e.g. "Ms.
# Al-Shabazz.", "THE COURT.", "Mr. Folly." — short, capitalized, ends in a
# period, up to ~4 words. If such a line follows a colon-prefixed line from a
# DIFFERENT speaker it would be wrongly merged (the known boundary gap's
# dangerous shape); this detects the shape so it can be flagged.
# A bare colon-less line that is just a speaker reference — e.g. "Ms.
# Al-Shabazz.", "THE COURT.", "Mr. Folly.", "The Witness." — short, a name
# only (no sentence text). If such a line follows a colon-prefixed line from a
# DIFFERENT speaker it would be wrongly merged (the known boundary gap's
# dangerous shape); this detects the shape so it can be flagged. Deliberately
# narrow:
#  - Court/role refs (THE COURT, THE WITNESS, ...) match UPPERCASE only — the
#    lowercase "the witness." continuation is a genuine continuation, not a flag.
#  - The Ms./Mr./Dr. branch matches title case with a SINGLE capitalized name
#    word ("Al-Shabazz", "Folly"); sentence text ("Mr. Locust, take the stand")
#    does not match.
_BARE_NAME_LINE_RE = re.compile(
    r"^(?:THE COURT|THE WITNESS|JUDGE|COURT|PROSECUTOR|GOVERNMENT)"
    r"(?:\s+[A-Z][A-Za-z0-9.'-]*)?\.?$"
    r"|^(?:MRS?\.|DR\.)(?:\s+[A-Z][A-Za-z0-9.'-]*)?\.?$",
    re.IGNORECASE,
)
# The Ms./Mr./Dr. branch matches title case ("Ms. Al-Shabazz."); the court/role
# branch must stay uppercase-only to reject lowercase continuations. Because
# re.IGNORECASE would loosen the role branch too, re-check role lines here.
_BARE_NAME_ROLE_RE = re.compile(
    r"^(?:(?:THE COURT|THE WITNESS|JUDGE|COURT|PROSECUTOR|GOVERNMENT|"
    r"The Court|The Witness|the Court|the Witness))"
    r"(?:\s+[A-Z][A-Za-z0-9.'-]*)?\.?$"
)


def _is_bare_name_line(line: str) -> bool:
    """True when `line` is a bare speaker reference that could be a wrongly
    merged boundary (see _BARE_NAME_LINE_RE). Role refs are uppercase-only;
    Ms./Mr./Dr. names are title-case with a single capitalized name word."""
    if _BARE_NAME_ROLE_RE.match(line):
        return True
    m = re.match(r"^(?:[Mm][Ss]\.|[Mm][Rr]\.|[Mm][Rr][Ss]\.|[Dd][Rr]\.)(?:\s+[A-Z][A-Za-z0-9.'-]*)?\.?$", line)
    return bool(m)


@dataclass
class Passage:
    """A run of consecutive lines spoken by one speaker on one page."""

    speaker: str
    page: int
    text: str
    kind: str = "spoken"  # "spoken" | "q" | "a"
    flags: list[str] = field(default_factory=list)


@dataclass
class TranscriptIndex:
    """A parsed transcript: ordered passages with page numbers + speaker map."""

    case: str = ""
    source: str = ""
    passages: list[Passage] = field(default_factory=list)

    def speaker_passages(self, name: str) -> list[Passage]:
        """Every passage spoken by `name` (case-insensitive substring on the
        speaker label). For an examining attorney this includes their Q. lines
        (attributed to them via the BY header), not just literal name prefixes."""
        nm = name.strip().upper()
        return [p for p in self.passages if nm in p.speaker.upper()]

    def speaker_pages(self, name: str) -> list[int]:
        return sorted({p.page for p in self.speaker_passages(name)})

    def flagged(self) -> list[Passage]:
        """Passages where a bare speaker-name-like line was merged as a
        continuation (the known boundary-gap dangerous shape). These are the
        SPECIFIC spots to cross-check against the source page — not everything."""
        return [p for p in self.passages if p.flags]

    def search(self, query: str) -> list[tuple[int, str]]:
        """Case-insensitive substring search over passage text -> (page, text)."""
        q = query.lower()
        return [(p.page, p.text) for p in self.passages if q in p.text.lower()]

    def speakers(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for p in self.passages:
            counts[p.speaker] = counts.get(p.speaker, 0) + len(p.text.split())
        return counts


def _strip_lineno(line: str) -> str:
    m = _LINENO_RE.match(line)
    return m.group(1) if m else line.strip()


def parse_transcript(text: str, case: str = "", source: str = "") -> TranscriptIndex:
    """Parse a court-reporter transcript string into a TranscriptIndex."""
    idx = TranscriptIndex(case=case, source=source)
    blocks = _FOOTER_RE.split(text)
    examiner: str | None = None  # current attorney owning the Q. lines
    current_speaker: str | None = None
    current_page = 0
    current_buf: list[str] = []
    current_flags: list[str] = []

    def flush() -> None:
        nonlocal current_speaker, current_buf, current_flags
        if current_speaker and current_buf:
            idx.passages.append(Passage(
                speaker=current_speaker,
                page=current_page,
                text=" ".join(current_buf).strip(),
                flags=list(current_flags),
            ))
        current_speaker = None
        current_buf = []
        current_flags = []

    first_page_found = False
    for block in blocks:
        # The page number is a standalone integer followed (possibly after
        # blank lines) by a volume-id line ("620\nJ59TDUN2", "1\nJ561dun1") or a
        # page-header line ("501\nJ591dun1  Nichols - Cross"). A leading
        # line-number column (1-25) is never followed by a volume id, so it is
        # not a page.
        page = 0
        blines = block.splitlines()
        for i, line in enumerate(blines):
            m = _PAGE_NO_RE.match(line)
            if not m:
                continue
            # scan forward past blanks for a volume-id/header line
            for j in range(i + 1, min(i + 4, len(blines))):
                nxt = blines[j].strip()
                if not nxt:
                    continue
                if _VOLUME_ID_RE.match(nxt) or _PAGE_HEADER_RE.match(nxt):
                    page = int(m.group(1))
                    break
                break
            if page:
                break
        if page:
            current_page = page
            first_page_found = True
        elif not first_page_found:
            # Leading header block (court caption, appearances) precedes the
            # first page marker — skip it rather than emitting page-0 passages.
            continue

        for raw in block.splitlines():
            line = _strip_lineno(raw).strip()
            if not line:
                continue
            if _STAGE_RE.match(line):
                flush()
                continue
            # Page-number and volume-id lines ("502", "J591dun1") and page
            # header lines ("J591dun1  Nichols - Cross") are layout, not speech.
            if _PAGE_NO_RE.match(line) or _VOLUME_ID_RE.match(line) or _PAGE_HEADER_RE.match(line):
                continue
            # "BY MS. AL-SHABAZZ:" — switches who owns the Q. lines.
            by = _BY_HEADER_RE.match(line)
            if by:
                flush()
                examiner = by.group(1).strip()
                continue
            # Section headers ("CROSS-EXAMINATION") are layout, not speech.
            if _SECTION_HEADER_RE.match(line) and len(line) < 40:
                flush()
                continue
            # Speaker-prefixed line: "THE COURT:", "MS. AL-SHABAZZ:", etc.
            sp = _SPEAKER_RE.match(line)
            if sp:
                flush()
                current_speaker = sp.group(1).strip()
                current_page = page if page else current_page
                current_buf.append(sp.group(2).strip())
                continue
            # Q. / A. examination lines — attributed to the examiner / witness.
            q = _Q_RE.match(line)
            if q:
                flush()
                current_speaker = examiner or "Q"
                current_page = page if page else current_page
                current_buf.append(q.group(1).strip())
                continue
            a = _A_RE.match(line)
            if a:
                flush()
                current_speaker = "THE WITNESS"
                current_page = page if page else current_page
                current_buf.append(a.group(1).strip())
                continue
            # Continuation of the current speaker's utterance.
            if current_speaker:
                # A bare colon-less line that looks like a speaker reference is
                # the known boundary-gap's dangerous shape: it may actually
                # belong to a DIFFERENT speaker and be wrongly merged here.
                if _is_bare_name_line(line):
                    current_flags.append(f"bare-name-line: {line}")
                current_buf.append(line)
            # else: pure layout/header text — skip.

    flush()
    return idx


def ingest_transcript_file(path: str | Path, case: str = "", source: str = "") -> TranscriptIndex:
    """Read a transcript text file (UTF-8) and parse it."""
    p = Path(path)
    return parse_transcript(p.read_text(encoding="utf-8", errors="replace"),
                            case=case or p.stem, source=str(p))
