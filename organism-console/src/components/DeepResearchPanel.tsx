import { useState } from "react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card"
import { Button } from "./ui/button"
import { Badge } from "./ui/badge"
import { ActionOnboardingCard } from "./ui/action-onboarding-card"
import { Search } from "lucide-react"

type Citation = { n: number; title: string; url: string }

type SubReport = {
  question: string
  answer: string
  citations: Citation[]
  degraded?: boolean
  note?: string
}

type DeepResearchResult = {
  status: string
  goal?: string
  iterations?: number
  sub_questions?: string[]
  sub_reports?: SubReport[]
  answer?: string
  citations?: Citation[]
  degraded?: boolean
  error?: string
}

type Props = { backendUrl: string }

export default function DeepResearchPanel({ backendUrl }: Props) {
  const [goal, setGoal] = useState("")
  const [result, setResult] = useState<DeepResearchResult | null>(null)
  const [running, setRunning] = useState(false)
  const [showUnits, setShowUnits] = useState(false)

  const runResearch = async () => {
    if (!goal.trim()) return
    setRunning(true)
    setResult(null)
    try {
      const r = await (await fetch(`${backendUrl}/features/deep-research`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ goal, max_sub_questions: 5, max_iterations: 2, max_results_per_unit: 5 }),
      })).json()
      setResult(r)
    } catch (e: any) {
      setResult({ status: "error", error: e.message })
    } finally {
      setRunning(false)
    }
  }

  return (
    <Card className="border-white/10 bg-panel xl:col-span-2">
      <CardHeader>
        <CardTitle>Deep Research — fan-out + iterative</CardTitle>
        <CardDescription>
          Manus-style Wide Research: the planner splits your goal into independent sub-questions, each researched in an
          isolated unit (search → read → cited sub-synthesis), a gap evaluator issues follow-ups, and a final synthesis
          merges everything into one cited report.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {!running && !result && (
          <ActionOnboardingCard
            id="deepresearch-quickstart"
            title="Deep Research Agent"
            description="The agent will spawn sub-researchers, execute parallel searches, read sources, and synthesize a final report. Try it out."
            actionLabel="Start Deep Research"
            icon={<Search size={20} />}
            onAction={() => {
              setGoal("Analyze SOTA UI trends for 2026")
              setTimeout(() => {
                const btn = document.getElementById("deepresearch-run-btn")
                if (btn) btn.click()
              }, 100)
            }}
          />
        )}
        <div className="flex gap-2">
          <input
            className="flex-1 rounded-md border border-white/10 bg-black/40 px-3 py-1.5 text-sm text-white/80"
            placeholder="e.g. build a comprehensive brief on the 2026 personal-AI-agent landscape, with sources"
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && runResearch()}
            disabled={running}
          />
          <Button id="deepresearch-run-btn" size="sm" disabled={running || !goal.trim()} onClick={runResearch}>
            {running ? "Researching…" : "Deep research"}
          </Button>
        </div>

        {running && (
          <div className="text-sm text-white/50">
            Fanning out sub-researchers… (each unit runs an isolated search → read → synthesis; a gap pass follows)
          </div>
        )}

        {result?.status === "error" && (
          <div className="rounded-xl border border-red-400/40 bg-red-400/10 p-3 text-sm text-red-200">
            {result.error}
          </div>
        )}

        {result && result.status !== "error" && (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <Badge className={result.status === "ok" ? "border-emerald-400/40 bg-emerald-400/10 text-emerald-300" : "border-amber-400/40 bg-amber-400/10 text-amber-300"}>
                {result.status}
              </Badge>
              {result.iterations != null && <Badge variant="outline">{result.iterations} iteration(s)</Badge>}
              {result.sub_questions && <Badge variant="outline">{result.sub_questions.length} sub-questions</Badge>}
              {result.degraded && <Badge variant="outline" className="text-amber-300">partial (some units degraded)</Badge>}
            </div>

            {result.answer && (
              <div className="rounded-xl border border-white/10 bg-black/30 p-3">
                <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-white/50">Final report</div>
                <div className="whitespace-pre-wrap text-sm text-white/85">{result.answer}</div>
              </div>
            )}

            {result.citations && result.citations.length > 0 && (
              <div>
                <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-white/50">Sources</div>
                <ul className="space-y-1 text-xs">
                  {result.citations.map((c) => (
                    <li key={c.n} className="text-white/60">
                      <span className="text-emerald-300">[{c.n}]</span> {c.title} —{" "}
                      <a href={c.url} target="_blank" rel="noreferrer" className="text-sky-300 hover:underline">{c.url}</a>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {result.sub_reports && result.sub_reports.length > 0 && (
              <>
                <button
                  className="text-xs font-semibold text-white/50 hover:text-white"
                  onClick={() => setShowUnits((s) => !s)}
                >
                  {showUnits ? "▾ hide sub-research units" : "▸ show sub-research units"}
                </button>
                {showUnits && (
                  <div className="space-y-3">
                    {result.sub_reports.map((r, i) => (
                      <div key={i} className="rounded-xl border border-white/10 bg-black/20 p-3">
                        <div className="mb-1 text-sm font-semibold text-emerald-200/90">
                          {r.question}
                          {r.degraded && <span className="ml-2 text-xs font-normal text-amber-300">degraded</span>}
                        </div>
                        <div className="whitespace-pre-wrap text-xs text-white/70">{r.answer || r.note}</div>
                      </div>
                    ))}
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
