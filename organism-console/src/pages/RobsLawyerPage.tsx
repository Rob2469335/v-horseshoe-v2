import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { useUiStore } from "../state/ui-store"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/card"
import { Button } from "../components/ui/button"
import { Badge } from "../components/ui/badge"

type AttorneyProfile = {
  key: string
  name: string
  represents: string
  word_count: number
  page_range: [number, number] | null
  objection_count: number
  examination_count: number
  objections: { page: number; text: string }[]
  examinations: { witness: string; page: number; kind: string }[]
  key_statements: { page: number; text: string }[]
}

type PhoneEvidenceEvent = {
  category: string
  day: string
  page: number
  speaker: string
  text: string
  context: { page: number; speaker: string; text: string }[]
  legal_question: string
}

type KeyEvent = {
  category: string
  day: string
  pages: string
  note: string
  passages: { page: number; speaker: string; text: string }[]
}

type TrialErrors = {
  ok: boolean
  flags: { category: string; page: number; speaker: string; text: string }[]
  key_events: KeyEvent[]
  phone_evidence_events: PhoneEvidenceEvent[]
  disclaimer: string
}

type Overview = {
  ok: boolean
  case: string
  days: { day: string; pages: number; page_min: number | null; page_max: number | null }[]
  total_passages: number
  total_pages: number
}

type SearchHit = { page: number; speaker: string; text: string; day: string }

type Tab = "tampering" | "appeal" | "your-counsel" | "phone-evidence" | "trial-qa" | "record-search" | "defense-errors"

const CLIENT_TABS: { id: Tab; label: string; desc: string }[] = [
  { id: "tampering", label: "Tampering & Avenues", desc: "The evidence-tampering claim, the case law, and every avenue open to you" },
  { id: "appeal", label: "Your Appeal", desc: "United States v. Rainford et al., No. 20-359 (2d Cir.) — what was and wasn't raised" },
  { id: "your-counsel", label: "Your Counsel", desc: "Dinnerstein & Cecutti — what they did on the record" },
  { id: "phone-evidence", label: "Phone Evidence", desc: "The government's phone/message evidence — selective or altered?" },
  { id: "defense-errors", label: "Defense Errors", desc: "Preserved errors & post-conviction review shapes" },
  { id: "trial-qa", label: "Ask About Your Trial", desc: "Page-cited answers from your actual transcript" },
  { id: "record-search", label: "Search the Record", desc: "Find any passage, page-cited" },
]

export default function RobsLawyerPage() {
  const backendUrl = useUiStore((state) => state.backendUrl)
  const [tab, setTab] = useState<Tab>("your-counsel")

  const overview = useQuery({
    queryKey: ["trial-overview", backendUrl],
    queryFn: async () => {
      const res = await fetch(`${backendUrl}/legal/trial/overview`, { headers: { Accept: "application/json" } })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      return (await res.json()) as Overview
    },
    staleTime: 60_000,
    retry: 1,
  })

  const attorneys = useQuery({
    queryKey: ["trial-attorneys", backendUrl],
    queryFn: async () => {
      const res = await fetch(`${backendUrl}/legal/trial/attorneys`, { headers: { Accept: "application/json" } })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const j = (await res.json()) as { ok: boolean; attorneys: AttorneyProfile[] }
      return j.attorneys
    },
    staleTime: 60_000,
    retry: 1,
  })

  const errors = useQuery({
    queryKey: ["trial-errors", backendUrl],
    queryFn: async () => {
      const res = await fetch(`${backendUrl}/legal/trial/errors`, { headers: { Accept: "application/json" } })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      return (await res.json()) as TrialErrors
    },
    staleTime: 60_000,
    retry: 1,
  })

  return (
    <div className="space-y-6 p-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">Rob's Lawyer</h1>
          <p className="max-w-3xl text-sm text-white/60">
            Your trial, analyzed from the record. <span className="text-white/40">Not legal advice</span> — this tool reports
            what the transcript shows and where, and flags the shapes a federal criminal-defense / §2255 review would
            investigate. Review with a qualified attorney before acting.
          </p>
        </div>
        {overview.data?.ok && (
          <div className="flex flex-wrap gap-2">
            <Badge className="border-white/20 bg-white/5 text-white/70">{overview.data.total_pages} transcript pages</Badge>
            <Badge className="border-white/20 bg-white/5 text-white/70">{overview.data.days.length} trial days</Badge>
          </div>
        )}
      </header>

      {overview.isError && (
        <Card className="border-amber-400/40 bg-amber-400/5">
          <CardContent className="py-4 text-sm text-amber-200">
            Trial record not loaded. Add your corrected transcript .txt files to{" "}
            <code className="rounded bg-black/40 px-1.5 py-0.5">data/legal/transcripts/</code>, then reload.
          </CardContent>
        </Card>
      )}

      <div className="flex flex-wrap gap-2">
        {CLIENT_TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            title={t.desc}
            className={`rounded-lg border px-3 py-1.5 text-sm transition-colors ${
              tab === t.id
                ? "border-emerald-400/50 bg-emerald-400/10 text-emerald-200"
                : "border-white/10 bg-white/5 text-white/70 hover:bg-white/10"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "tampering" && <TamperingTab />}
      {tab === "appeal" && <AppealTab />}
      {tab === "your-counsel" && <YourCounselTab attorneys={attorneys.data} loading={attorneys.isLoading} />}
      {tab === "phone-evidence" && <PhoneEvidenceTab data={errors.data} loading={errors.isLoading} />}
      {tab === "defense-errors" && <DefenseErrorsTab data={errors.data} loading={errors.isLoading} />}
      {tab === "trial-qa" && <TrialQATab backendUrl={backendUrl} />}
      {tab === "record-search" && <RecordSearchTab backendUrl={backendUrl} />}
    </div>
  )
}

function Loading() {
  return <div className="text-sm text-white/50">Loading the trial record…</div>
}

function AttorneyCard({ a, highlighted }: { a: AttorneyProfile; highlighted: boolean }) {
  const objections = a.objections.slice(0, 8)
  const exams = a.examinations.slice(0, 8)
  const roleLabel = highlighted ? "YOUR COUNSEL" : a.represents
  return (
    <Card className={`border-white/10 ${highlighted ? "bg-emerald-950/20" : "bg-panel"}`}>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle className="text-lg">{a.name}</CardTitle>
          <Badge className={highlighted ? "border-emerald-400/40 bg-emerald-400/10 text-emerald-300" : "border-white/20 bg-white/5 text-white/60"}>
            {roleLabel}
          </Badge>
        </div>
        <CardDescription>
          {a.word_count.toLocaleString()} words spoken · objections {a.objection_count} · examinations {a.examination_count}
          {a.page_range ? ` · pages ${a.page_range[0]}-${a.page_range[1]}` : ""}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {a.key_statements.length > 0 && (
          <div>
            <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-white/50">Key moments</div>
            <div className="space-y-2">
              {a.key_statements.slice(0, 3).map((s, i) => (
                <div key={i} className="rounded-lg border border-white/10 bg-black/20 p-2 text-sm">
                  <span className="mr-2 text-white/40">p.{s.page}</span>
                  <span className="text-white/80">{s.text.slice(0, 180)}</span>
                </div>
              ))}
            </div>
          </div>
        )}
        {objections.length > 0 && (
          <div>
            <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-white/50">Objections</div>
            <div className="space-y-1.5">
              {objections.map((o, i) => (
                <div key={i} className="rounded bg-black/20 px-2 py-1 text-sm text-white/70">
                  <span className="mr-2 text-white/40">p.{o.page}</span>
                  {o.text.slice(0, 120)}
                </div>
              ))}
            </div>
          </div>
        )}
        {exams.length > 0 && (
          <div>
            <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-white/50">Witness examinations</div>
            <div className="space-y-1">
              {exams.slice(0, 6).map((e, i) => (
                <div key={i} className="rounded bg-black/10 px-2 py-1 text-xs text-white/60">
                  <span className="mr-2 text-white/40">p.{e.page}</span>
                  {e.witness}
                </div>
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function TamperingTab() {
  const facts: { page: string; text: string }[] = [
    { page: "p.1745", text: "GX 710A shows an 'ABC' editing cursor on the contact; 710B does not. The Court confirmed it on the record: 'in 710A it says ABC and in 710B, it does not say ABC.'" },
    { page: "p.1746", text: "'Can we put up 535 next to 710B ... how come it's a different phone number? ... The numbers don't add up.'" },
    { page: "pp.873-878", text: "The phone (1B-29) sat at an FBI agent's desk ~a month, powered-state unknown, no markings." },
    { page: "pp.1111-1113", text: "Your counsel Dinnerstein objected to 710A/B, got a voir dire, Kalkanis couldn't recall the timing — then withdrew the objection." },
    { page: "No. 20-359", text: "The appeal (2d Cir. 2024) never raised any of it — zero mentions of tampering, ABC, chain of custody, or exhibit 710." },
  ]
  const cases: { cite: string; rule: string }[] = [
    { cite: "Napue v. Illinois, 360 U.S. 264 (1959)", rule: "A conviction obtained through the government's knowing use of false testimony violates due process." },
    { cite: "United States v. Alston, 899 F.3d 135, 146 (2d Cir. 2018)", rule: "'The government may not knowingly introduce false evidence or testimony to obtain a conviction.' Quoted in your own appeal." },
    { cite: "Arizona v. Youngblood, 488 U.S. 51 (1988)", rule: "Bad faith required for lost/destroyed evidence — but your claim is about evidence that was PRESENT and visibly altered, which is treated differently (Napue/Alston)." },
    { cite: "Brady v. Maryland, 373 U.S. 83 (1963)", rule: "Suppression of material exculpatory evidence violates due process regardless of good faith." },
    { cite: "Schlup v. Delo, 513 U.S. 298 (1995)", rule: "The actual-innocence gateway: new reliable evidence, more likely than not no reasonable juror would convict — can overcome the time bar (McQuiggin v. Perkins, 569 U.S. 383)." },
    { cite: "Holland v. Florida, 560 U.S. 631 (2010)", rule: "Equitable tolling if extraordinary circumstances (an abandoned client) and diligence." },
  ]
  const avenues = [
    { title: "§2255 — Ineffective Assistance of Appellate Counsel", strength: "Strongest", text: "Unger raised ordinary issues but never the tampered-phone evidence — your strongest preserved issue, specifically identified. Requires showing the omission fell below a reasonable standard AND the appeal probably would have succeeded." },
    { title: "Schlup actual-innocence gateway", strength: "The time-bar path", text: "Because your deadline passed, this is how a court reaches the merits — needs NEW reliable evidence (an expert on the actual 710A/710B exhibits showing recent editing)." },
    { title: "Fabricated/tampered evidence (Napue / Alston due process)", strength: "Strongest legal fit", text: "If the phone evidence was knowingly presented as genuine when altered, that's a due-process violation at the core of the trial. This is what your appeal should have raised." },
    { title: "Brady suppression", strength: "Secondary", text: "p.1549 (Court-elicited): 'many, many more emails than have been admitted' existed. If favorable material was suppressed, that's Brady." },
  ]
  return (
    <div className="space-y-4">
      <Card className="border-amber-400/30 bg-amber-400/5">
        <CardHeader>
          <CardTitle>Evidence tampering — the claim, the law, and your avenues</CardTitle>
          <CardDescription>
            The best-lawyer analysis, grounded in your record and the governing federal case law. Not legal advice.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="rounded-lg border border-amber-400/40 bg-black/30 p-3 text-sm">
            <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-amber-200">One-page summary — give this to your attorney</div>
            <p className="text-white/80">
              The phone evidence linking you to the scheme was <strong>court-acknowledged as visibly altered</strong> (ABC cursor,
              p.1745; number mismatch, p.1746), with <strong>broken chain of custody</strong> (pp.873-878). Your trial counsel
              objected then withdrew (pp.1111-1113); you twice asked the court to remove them (pp.722-725); the <strong>appeal
              never raised any of it</strong> (110 F.4th 455). Authorities: <strong>Napue</strong>, <strong>Alston</strong> (fabricated
              evidence = due process), <strong>Schlup</strong>/<strong>McQuiggin</strong> (actual-innocence gateway past the time bar).
              Decisive next step: <strong>an expert must examine the actual 710A/710B exhibits.</strong>
            </p>
            <p className="mt-2 text-xs text-amber-200/80">
              Full summary sheet: data/legal/summary_sheet_for_attorney.md · Full packets: deepdive_evidence_tampering_avenues.md,
              deepdive_counsel_abandonment.md, your_appeal_findings.md
            </p>
          </div>
        </CardContent>
      </Card>

      <Card className="border-white/10 bg-panel">
        <CardHeader><CardTitle className="text-base">The record facts</CardTitle></CardHeader>
        <CardContent className="space-y-2">
          {facts.map((f, i) => (
            <div key={i} className="rounded-lg border border-white/10 bg-black/20 p-2 text-sm">
              <span className="mr-2 text-white/40">{f.page}</span>
              <span className="text-white/80">{f.text}</span>
            </div>
          ))}
        </CardContent>
      </Card>

      <Card className="border-white/10 bg-panel">
        <CardHeader><CardTitle className="text-base">Your counsel record — the substitution denials</CardTitle></CardHeader>
        <CardContent className="space-y-2">
          <div className="rounded-lg border border-white/10 bg-black/20 p-2 text-sm">
            <span className="mr-2 text-white/40">p.722</span>
            <span className="text-white/80">You, on the record, mid-trial: "I would like to relieve my lawyers on duty because they're not representing me adequately." Court: "I'm not going to relieve them in the middle of the trial. The application denied."</span>
          </div>
          <div className="rounded-lg border border-white/10 bg-black/20 p-2 text-sm">
            <span className="mr-2 text-white/40">p.722</span>
            <span className="text-white/80">You: "They convicted me."</span>
          </div>
          <div className="rounded-lg border border-white/10 bg-black/20 p-2 text-sm">
            <span className="mr-2 text-white/40">p.723</span>
            <span className="text-white/80">Court: "I find the representation of Mr. Cecutti and Mr. Dinnerstein to be quite good at this point... I'm denying that application."</span>
          </div>
          <div className="rounded-lg border border-white/10 bg-black/20 p-2 text-sm">
            <span className="mr-2 text-white/40">p.724-725</span>
            <span className="text-white/80">You: "I cannot fight against the lawyers and the government."</span>
          </div>
          <div className="rounded-lg border border-white/10 bg-black/20 p-2 text-sm">
            <span className="mr-2 text-white/40">p.947</span>
            <span className="text-white/80">Court asked if you wanted to continue with Dinnerstein and Cecutti; you said yes.</span>
          </div>
          <p className="rounded-lg border border-white/10 bg-black/10 p-2 text-xs text-white/50">
            Your distrust of your own counsel is documented and preserved. The court retained them twice, and the Second Circuit
            upheld the denial on appeal — but this record, combined with the tampered-phone evidence your counsel never raised,
            is the substance of a §2255 ineffective-assistance claim. (The "government gave me two lawyers to punish me" theory
            is NOT established by the record — the record shows the opposite: you tried to remove them.)
          </p>
        </CardContent>
      </Card>

      <Card className="border-white/10 bg-panel">
        <CardHeader><CardTitle className="text-base">The governing case law (verified)</CardTitle></CardHeader>
        <CardContent className="space-y-2">
          {cases.map((c, i) => (
            <div key={i} className="rounded-lg border border-white/10 bg-black/20 p-2 text-sm">
              <span className="block text-white/40">{c.cite}</span>
              <span className="text-white/80">{c.rule}</span>
            </div>
          ))}
        </CardContent>
      </Card>

      <Card className="border-white/10 bg-panel">
        <CardHeader><CardTitle className="text-base">Every avenue, ranked</CardTitle></CardHeader>
        <CardContent className="space-y-2">
          {avenues.map((a, i) => (
            <div key={i} className="rounded-lg border border-white/10 bg-black/20 p-2">
              <div className="mb-1 flex items-center gap-2">
                <span className="text-sm font-semibold text-white/90">{a.title}</span>
                <span className="rounded bg-amber-400/20 px-1.5 py-0.5 text-xs text-amber-200">{a.strength}</span>
              </div>
              <p className="text-sm text-white/70">{a.text}</p>
            </div>
          ))}
        </CardContent>
      </Card>

      <p className="rounded-lg border border-amber-400/40 bg-amber-400/5 p-3 text-sm text-amber-200">
        The decisive next step is an expert examination of the actual 710A/710B exhibits — turning the argued ABC
        difference into new reliable evidence for the Schlup gateway. This analysis is record-based and is not legal
        advice; a qualified post-conviction attorney or federal habeas clinic should review before any filing.
      </p>
    </div>
  )
}

function AppealTab() {
  return (
    <div className="space-y-4">
      <Card className="border-emerald-400/30 bg-emerald-950/20">
        <CardHeader>
          <CardTitle>United States v. Rainford et al., No. 20-359 (2d Cir.)</CardTitle>
          <CardDescription>
            Your appeal: argued May 1, 2023, decided August 2, 2024 — 110 F.4th 455. Panel: Jacobs, Menashi, Merriam.
            You (Robert Locust), Ryan Rainford, and Bryan Duncan are the named appellants. Your appellate counsel:
            Randall Douglas Unger.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-white/50">Outcome for you</div>
            <ul className="list-disc space-y-1 pl-5 text-sm text-white/80">
              <li>Conviction <strong>affirmed</strong> (all trial/conviction issues rejected)</li>
              <li>Guidelines affirmed, but <strong>remanded for factfinding</strong> on the number of fraudulent accidents during your tenure (loss enhancement)</li>
              <li>Restitution affirmed but <strong>reduced by $120,000</strong></li>
            </ul>
          </div>
          <div>
            <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-white/50">What was raised</div>
            <ul className="list-disc space-y-1 pl-5 text-sm text-white/80">
              <li>Denial of your motion to substitute trial counsel (rejected)</li>
              <li>Prosecutorial vouching (Tucker, Dewitt, Kalkanis, Martin) — plain error</li>
              <li>Prosecutor denigrating the defense ("total sideshow") — plain error</li>
              <li>Sufficiency — no proof the recruits' claims were fraudulent</li>
              <li>Burden of proof — "even if Locust did not know for certain" closing</li>
            </ul>
          </div>
          <div>
            <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-white/50">What was NOT raised (verified — 0 occurrences in the opinion)</div>
            <ul className="list-disc space-y-1 pl-5 text-sm text-amber-200">
              <li>Tampering / altered evidence — <strong>never raised</strong></li>
              <li>The ABC editing-cursor demonstration (p.1745, 710A vs 710B) — <strong>never raised</strong></li>
              <li>Chain of custody gaps (pp.873-878) — <strong>never raised</strong></li>
              <li>Your trial counsel's withdrawal of the 710A/B objection after voir dire (p.1113) — <strong>never raised</strong></li>
              <li>The phone-number mismatch (p.1746, "the numbers don't add up") — <strong>never raised</strong></li>
            </ul>
          </div>
          <p className="rounded-lg border border-amber-400/40 bg-amber-400/5 p-3 text-sm text-amber-200">
            This combination — an appellate lawyer who raised ordinary issues but never your strongest preserved
            phone-evidence attack, and who (per your account) would not visit or take your calls — is the shape of a
            claim of <strong>ineffective assistance of appellate counsel</strong> under §2255. The §2255 one-year clock
            may be running from when judgment became final. <strong>Check the deadline immediately.</strong> This is
            record analysis, not legal advice — review with a qualified post-conviction attorney before filing.
          </p>
        </CardContent>
      </Card>
    </div>
  )
}

function YourCounselTab({ attorneys, loading }: { attorneys?: AttorneyProfile[]; loading: boolean }) {
  if (loading || !attorneys) return <Loading />
  const mine = attorneys.filter((a) => a.represents === "Robert Locust")
  const coDef = attorneys.filter((a) => a.represents !== "Robert Locust" && !a.represents.startsWith("Government"))
  const gov = attorneys.filter((a) => a.represents.startsWith("Government"))
  return (
    <div className="space-y-6">
      <div>
        <h2 className="mb-2 text-lg font-semibold">Your counsel</h2>
        <p className="mb-3 text-sm text-white/50">
          From the record, Dinnerstein and Cecutti represented you (Robert Locust). The appearances block on page 25 lists
          them; Dinnerstein's opening at page 65 discusses Mr. Locust.
        </p>
        <div className="grid gap-4 md:grid-cols-2">
          {mine.map((a) => <AttorneyCard key={a.key} a={a} highlighted />)}
        </div>
      </div>
      <div>
        <h2 className="mb-2 text-lg font-semibold">Co-defendant counsel</h2>
        <div className="grid gap-4 md:grid-cols-2">
          {coDef.map((a) => <AttorneyCard key={a.key} a={a} highlighted={false} />)}
        </div>
      </div>
      {gov.length > 0 && (
        <div>
          <h2 className="mb-2 text-lg font-semibold">Government (AUSAs)</h2>
          <div className="grid gap-4 md:grid-cols-3">
            {gov.map((a) => <AttorneyCard key={a.key} a={a} highlighted={false} />)}
          </div>
        </div>
      )}
    </div>
  )
}

function PhoneEvidenceTab({ data, loading }: { data?: TrialErrors; loading: boolean }) {
  if (loading || !data) return <Loading />
  const events = data.phone_evidence_events.filter((e) => e.speaker.toUpperCase().includes("AL-SHABAZZ") || e.page > 1000)
  return (
    <div className="space-y-4">
      <Card className="border-amber-400/30 bg-amber-400/5">
        <CardContent className="py-4 text-sm text-amber-100/90">
          <strong className="text-amber-200">Phone / message evidence challenge.</strong> On 5/20, cross-examining the
          government's phone-evidence witness, the defense challenged the completeness and handling of the phone, email and
          text evidence — whether messages were "selectively left out," whether deleted messages were recovered, and how
          much the government put in versus left out. The passages below are from the record.
        </CardContent>
      </Card>
      {events.length === 0 && <p className="text-sm text-white/50">No phone-evidence challenge passages found.</p>}
      {events.map((e, i) => (
        <Card key={i} className="border-white/10 bg-panel">
          <CardContent className="py-3">
            <div className="mb-1 flex flex-wrap items-center gap-2">
              <Badge className="border-white/20 bg-white/5 text-white/60">p.{e.page}</Badge>
              <span className="text-xs text-white/50">{e.speaker} · {e.day}</span>
            </div>
            <p className="text-sm text-white/85">{e.text}</p>
            {e.context && e.context.length > 0 && (
              <div className="mt-2 space-y-1 rounded bg-black/20 p-2">
                {e.context.map((c, j) => (
                  <div key={j} className="text-xs text-white/55">
                    <span className="mr-1 text-white/35">p.{c.page}</span>
                    <span className="mr-1 text-white/45">{c.speaker}:</span>
                    {c.text.slice(0, 150)}
                  </div>
                ))}
              </div>
            )}
            <p className="mt-2 text-xs italic text-white/40">{e.legal_question}</p>
          </CardContent>
        </Card>
      ))}
    </div>
  )
}

function DefenseErrorsTab({ data, loading }: { data?: TrialErrors; loading: boolean }) {
  if (loading || !data) return <Loading />
  const byCat: Record<string, typeof data.flags> = {}
  for (const f of data.flags) (byCat[f.category] ??= []).push(f)
  return (
    <div className="space-y-4">
      <p className="text-sm text-white/50">{data.disclaimer}</p>
      {Object.entries(byCat).map(([cat, flags]) => (
        <Card key={cat} className="border-white/10 bg-panel">
          <CardHeader>
            <CardTitle className="text-base">{cat}</CardTitle>
            <CardDescription>{flags.length} passage(s) in the record</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {flags.slice(0, 10).map((f, i) => (
              <div key={i} className="rounded bg-black/20 px-2 py-1 text-sm text-white/70">
                <span className="mr-2 text-white/40">p.{f.page}</span>
                <span className="mr-2 text-white/50">{f.speaker}</span>
                {f.text.slice(0, 140)}
              </div>
            ))}
          </CardContent>
        </Card>
      ))}
    </div>
  )
}

function TrialQATab({ backendUrl }: { backendUrl: string }) {
  const [q, setQ] = useState("")
  const [answer, setAnswer] = useState<string | null>(null)
  const [hits, setHits] = useState<SearchHit[]>([])
  const [loading, setLoading] = useState(false)

  async function ask() {
    if (!q.trim() || !backendUrl) return
    setLoading(true)
    try {
      const res = await fetch(`${backendUrl}/legal/trial/search`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ query: q, limit: 15 }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const j = (await res.json()) as { ok: boolean; hits: SearchHit[] }
      setHits(j.hits)
      if (j.hits.length === 0) {
        setAnswer("No passages found in the trial record for that. Try different wording.")
      } else {
        setAnswer(null)
      }
    } catch (e) {
      setAnswer(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-4">
      <Card className="border-white/10 bg-panel">
        <CardHeader>
          <CardTitle>Ask about your trial</CardTitle>
          <CardDescription>
            Search the full record (all days, page-cited). The hits are the actual transcript passages — a qualified
            attorney would read them with the legal framework (Strickland, preserved errors, discovery) in mind.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <textarea
            className="w-full min-h-[90px] rounded-xl border border-white/10 bg-black/30 p-3 text-sm text-white/90 focus:outline-none focus:border-white/30"
            placeholder='e.g. "the phone evidence" · "what did Dinnerstein object to" · "Ms. Al-Shabazz about deleted messages"'
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <Button onClick={ask} disabled={loading || !q.trim()}>{loading ? "Searching…" : "Search the record"}</Button>
          {answer && <p className="text-sm text-white/70">{answer}</p>}
          {hits.length > 0 && (
            <div className="space-y-2">
              {hits.map((h, i) => (
                <div key={i} className="rounded-lg border border-white/10 bg-black/20 p-2 text-sm">
                  <div className="mb-1 flex flex-wrap gap-2 text-xs text-white/40">
                    <span>p.{h.page}</span>
                    <span>{h.speaker}</span>
                    <span>{h.day}</span>
                  </div>
                  <p className="text-white/80">{h.text}</p>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function RecordSearchTab({ backendUrl }: { backendUrl: string }) {
  const [q, setQ] = useState("")
  const [hits, setHits] = useState<SearchHit[]>([])
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function run() {
    if (!q.trim() || !backendUrl) return
    setLoading(true)
    setErr(null)
    try {
      const res = await fetch(`${backendUrl}/legal/trial/search`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ query: q, limit: 40 }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setHits(((await res.json()) as { hits: SearchHit[] }).hits)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <Card className="border-white/10 bg-panel">
      <CardHeader>
        <CardTitle>Search the trial record</CardTitle>
        <CardDescription>Page-cited passages across all days of your transcript.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <textarea
          className="w-full min-h-[70px] rounded-xl border border-white/10 bg-black/30 p-3 text-sm text-white/90 focus:outline-none focus:border-white/30"
          placeholder='e.g. "Brady" · "chain of custody" · "Kalkanis" · "phone was powered"'
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <Button onClick={run} disabled={loading || !q.trim()}>{loading ? "Searching…" : "Search"}</Button>
        {err && <p className="text-sm text-red-300">{err}</p>}
        {hits.map((h, i) => (
          <div key={i} className="rounded-lg border border-white/10 bg-black/20 p-2 text-sm">
            <div className="mb-1 flex flex-wrap gap-2 text-xs text-white/40">
              <span>p.{h.page}</span><span>{h.speaker}</span><span>{h.day}</span>
            </div>
            <p className="text-white/80">{h.text}</p>
          </div>
        ))}
      </CardContent>
    </Card>
  )
}
