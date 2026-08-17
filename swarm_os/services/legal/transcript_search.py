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

_FOOTER_RE = re.compile(
    r"SOUTHERN DISTRICT REPORTERS, P\.C\.[^\n]*(?:\n\s*\(212\)\s*805-0300\s*)?"
)
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
# The witness name printed at the top of each page of testimony, e.g.
# "J5ATDUN1  Mueller - Cross" -> "Mueller", "J58TDUN1  Dewitt" -> "Dewitt".
# Exactly ONE name token is captured (this corpus uses single-token names:
# Nichols, Dewitt, Mueller, Al-Shabazz, ...); the section label ("- Cross",
# "- Direct", ...) is stripped and never folds into the name. The
# "J571dun1 - Corrected" shape (dash BEFORE any name) does not match, so it
# never produces a bogus "Corrected" witness.
_WITNESS_NAME_RE = re.compile(
    r"^[Jj]\d+[A-Za-z]+\d+[A-Za-z]*\d*(?:Page)?\s+"
    r"([A-Z][A-Za-z0-9.'-]*)"
    r"(?:\s*-\s+.*)?$"
)
# Section labels that can never be a witness name (defense-in-depth: a
# "J591dun1  Direct" style header — a bare section keyword with no name —
# would otherwise be captured as a bogus witness).
_SECTION_KEYWORDS = frozenset(
    {
        "Direct",
        "Cross",
        "Redirect",
        "Recross",
        "Examination",
        "Voir",
        "DirectExamination",
        "CrossExamination",
    }
)
_STAGE_RE = re.compile(
    r"^\((?:Jury|Witness|Recess|Continued|At sidebar|In open court|In open c)[^)]*\)$",
    re.I,
)
# Examination section headers that are layout, not speech ("CROSS-EXAMINATION",
# "DIRECT EXAMINATION", "REDIRECT EXAMINATION", "RECROSS EXAMINATION").
_SECTION_HEADER_RE = re.compile(
    r"^(?:CROSS|DIRECT|REDIRECT|RECROSS|VOIR DIRE)?[-\s]*(?:EXAMINATION|QUESTIONS BY)\b",
    re.I,
)
# Summation / Charge section headers carry the speaker in the header itself,
# e.g. "J5l1dun2   Summation - Ms. Rothman" or "J5m1dun1  Charge". The body is
# continuous argument/instruction text with NO per-line "NAME:" prefixes, so we
# attribute the following text to the named speaker (or THE COURT for a Charge).
_SUMMATION_HEADER_RE = re.compile(
    r"^(?:[A-Za-z0-9 ]+\s+)?(Summation|Closing Argument|Charge)\s*-\s*([A-Z][A-Za-z .'-]+?)\s*$",
    re.I,
)
_CHARGE_HEADER_RE = re.compile(r"^(?:[A-Za-z0-9 ]+\s+)?Charge\s*$", re.I)
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
    m = re.match(
        r"^(?:[Mm][Ss]\.|[Mm][Rr]\.|[Mm][Rr][Ss]\.|[Dd][Rr]\.)(?:\s+[A-Z][A-Za-z0-9.'-]*)?\.?$",
        line,
    )
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
    # page -> witness name, harvested from each page's header layout line
    # ("J5ATDUN1  Mueller - Cross" -> {760: "Mueller"}). The court reporter
    # prints the testifying witness's name at the top of each page of
    # testimony; this lets the analysis layer group passages by witness.
    witness_names: dict[int, str] = field(default_factory=dict)

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

    def flush(*, keep_speaker: bool = False) -> None:
        nonlocal current_speaker, current_buf, current_flags
        if current_speaker and current_buf:
            idx.passages.append(
                Passage(
                    speaker=current_speaker,
                    page=current_page,
                    text=" ".join(current_buf).strip(),
                    flags=list(current_flags),
                )
            )
        if keep_speaker:
            # Preserve the speaker across a page boundary so CONTINUOUS speech
            # (the Court reading instructions, a summation) keeps flowing into
            # the same speaker's passages on the new page.
            current_buf = []
            current_flags = []
        else:
            current_speaker = None
            current_buf = []
            current_flags = []

    first_page_found = False
    pending_tail_page = 0  # pypdf-extracted pages print the page number BEFORE
    # the footer, so after a footer split it sits at the END of the previous
    # block. We harvest it from EVERY block (including the caption block) and
    # apply it to the NEXT block's content.
    _last_page = 0  # last page whose content was processed (for page-change flush)
    for block in blocks:
        blines = block.splitlines()
        # Tail-harvest FIRST (before any first_page_found continue): the page
        # number is the last non-blank line of this block in pypdf layout, and
        # it belongs to the NEXT block's content. Harvesting here guarantees the
        # caption block (block 0, no content) still seeds the first content page.
        tail_page = 0
        for line in reversed(blines):
            tm = _PAGE_NO_RE.match(line)
            if tm:
                tail_page = int(tm.group(1))
                break

        # The page number is a standalone integer followed (possibly after
        # blank lines) by a volume-id line ("620\nJ59TDUN2", "1\nJ561dun1") or a
        # page-header line ("501\nJ591dun1  Nichols - Cross"). A leading
        # line-number column (1-25) is never followed by a volume id, so it is
        # not a page.
        page = 0
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
        if not page and pending_tail_page:
            # pypdf-extracted layout: the page number was harvested from the
            # previous block's tail (it sat just before that page's footer), and
            # THIS block is that page's content. Apply it directly.
            page = pending_tail_page
        pending_tail_page = tail_page  # seed the NEXT block's page
        if page and page != _last_page:
            # A new page begins. In CONTINUOUS speech (the Court reading
            # instructions, a summation) there is no "NAME:" prefix line to
            # trigger a flush at the page boundary — so the previous page's
            # trailing passage would swallow this page's text and stamp the
            # prior page's number on it. Flush now so the new page starts a
            # fresh passage (the page-break utterance belongs to the page where
            # it BEGAN — same rule as the speaker/Q-A branches).
            flush(keep_speaker=True)
            # The new page's continuous speech begins NOW; advance current_page
            # so passages starting on this page are stamped with THIS page number.
            current_page = page
        if page:
            # NOTE: `current_page` is deliberately NOT advanced here. A passage
            # is flushed lazily — only when the NEXT speaker/stage/section line
            # arrives, which may be in the following page's block. Advancing
            # current_page at this block top would stamp the previous page's
            # trailing passage with the NEXT page's number (every page's final
            # line bleeds one page forward — a pre-existing bug that silently
            # misdated the page anchors across all artifacts). The speaker/Q/A
            # branches below set `current_page` from `page` when the passage
            # BEGINS, which is the correct attribution point (a page-break-split
            # utterance belongs to the page where it started).
            first_page_found = True
        elif not first_page_found:
            # Leading header block (court caption, appearances) precedes the
            # first page marker — skip it rather than emitting page-0 passages.
            continue
        if page:
            _last_page = page

        for raw in block.splitlines():
            line = _strip_lineno(raw).strip()
            if not line:
                continue
            if _STAGE_RE.match(line):
                flush()
                continue
            # Summation / Charge section headers BEFORE the generic page-header
            # check (which would otherwise swallow "J5l1dun2 Summation - Mr.
            # Dinnerstein" as a witness header). The body is CONTINUOUS argument/
            # instruction text with no per-line "NAME:" prefixes; attribute it to
            # the speaker named in the header, or THE COURT for a Charge.
            sm = _SUMMATION_HEADER_RE.match(line)
            if sm:
                flush()
                current_speaker = sm.group(2).strip().upper()
                current_page = page if page else current_page
                continue
            if _CHARGE_HEADER_RE.match(line):
                flush()
                current_speaker = "THE COURT"
                current_page = page if page else current_page
                continue
            # Page-number and volume-id lines ("502", "J591dun1") and page
            # header lines ("J591dun1  Nichols - Cross") are layout, not speech.
            if (
                _PAGE_NO_RE.match(line)
                or _VOLUME_ID_RE.match(line)
                or _PAGE_HEADER_RE.match(line)
            ):
                # A page-header line names the testifying witness ("J5ATDUN1
                #  Mueller - Cross" -> "Mueller"). Harvest it so the analysis
                # layer can group passages by witness. The leading page-number/
                # volume-id lines match nothing, so they leave the map untouched.
                wm = _WITNESS_NAME_RE.match(line)
                if wm and page and wm.group(1) not in _SECTION_KEYWORDS:
                    idx.witness_names[page] = wm.group(1).strip()
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


def ingest_transcript_file(
    path: str | Path, case: str = "", source: str = ""
) -> TranscriptIndex:
    """Read a transcript text file (UTF-8) and parse it. Auto-detects the
    SDNY court-reporter format vs the Min-U-Script (transcript-agency) export."""
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="replace")
    if "Min-U-Script" in text[:4000] or re.search(
        r"\bJ\d+[A-Za-z]*\d*(?:VD)?\s*Page\s*\d+", text[:4000]
    ):
        return parse_minuscript(text, case=case or p.stem, source=str(p))
    return parse_transcript(text, case=case or p.stem, source=str(p))


# Min-U-Script (transcript-agency export) format — e.g. the voir-dire / pre-trial
# day (5/6/19) which is NOT the SDNY court-reporter layout. Each content "page"
# is a single line:  "J561dun1Page 3 1  <text> 2  <text> 3  <text> ..." —
# a page marker (volume-id + "Page N") followed by inline "linenum text" runs.
# There is NO reporter footer to split on, so page identity comes from the
# "Page N" marker directly. A trailing word index ("ago (4) 12:24;...") is not
# speech and is dropped.
_MINUSCRIPT_PAGE_RE = re.compile(
    r"^.*?\bJ\d+[A-Za-z]*\d*(?:VD)?(?:\s+(?:[A-Za-z ]+\s*))?Page\s*(\d+)\s+", re.I
)
_MINUSCRIPT_LINENO_SPLIT_RE = re.compile(r"(\d{1,2})\s{2,}")


def parse_minuscript(text: str, case: str = "", source: str = "") -> TranscriptIndex:
    """Parse a Min-U-Script transcript-agency export into a TranscriptIndex."""
    idx = TranscriptIndex(case=case, source=source)
    current_speaker: str | None = None
    current_page = 0
    current_buf: list[str] = []
    current_flags: list[str] = []

    def flush() -> None:
        nonlocal current_speaker, current_buf, current_flags
        if current_speaker and current_buf:
            idx.passages.append(
                Passage(
                    speaker=current_speaker,
                    page=current_page,
                    text=" ".join(current_buf).strip(),
                    flags=list(current_flags),
                )
            )
        current_speaker = None
        current_buf = []
        current_flags = []

    in_word_index = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        # Word index section begins (e.g. "ago (4)    12:24;...") — stop.
        if re.match(r"^[A-Za-z][A-Za-z .'-]*\(\d+\)\s+\d{1,3}:\d{1,2}", line):
            in_word_index = True
        if in_word_index:
            continue
        # Page marker line: "J561dun1Page 3 1  <content> 2  <content>..."
        m = _MINUSCRIPT_PAGE_RE.match(line)
        if m:
            flush()
            current_page = int(m.group(1))
            # Reconstruct the spoken content: strip the page marker, then split
            # into (lineno, text) runs. The inline line numbers delimit turns.
            body = line[m.end() :]
            runs = _MINUSCRIPT_LINENO_SPLIT_RE.split(body)
            pieces = []
            for r in runs:
                if r.strip().isdigit():
                    continue
                pieces.append(r)
            clean = " ".join(pieces)

            # Walk the cleaned text, splitting on speaker prefixes into passages.
            parts = re.split(
                r"(?=(?:^|\s)(THE COURT|THE WITNESS|THE REPORTER|THE LAW CLERK|"
                r"THE DEPUTY CLERK|THE DEFENDANT|JUROR|MR\. [A-Z]+|MS\. [A-Z]+|"
                r"BY (?:MR\.|MS\.) [A-Z-]+):\s)",
                clean,
            )
            for part in parts:
                part = part.strip()
                if not part:
                    continue
                sp = _SPEAKER_RE.match(part)
                if sp:
                    flush()
                    current_speaker = sp.group(1).strip()
                    current_page = current_page
                    current_buf = [sp.group(2).strip()]
                    continue
                if current_speaker:
                    current_buf.append(part)
            continue
        # Non-page line in a Min-U-Script file (caption/continuation) — treat
        # as continuation text if a speaker is open, else skip.
        if current_speaker:
            current_buf.append(line)

    flush()
    return idx
