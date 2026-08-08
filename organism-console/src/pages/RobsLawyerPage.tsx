import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { useUiStore } from "../state/ui-store"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/card"
import { Button } from "../components/ui/button"
import { Badge } from "../components/ui/badge"

type Citation = {
  raw: string
  kind: string
  verified: boolean
  status: number | null
  error_message: string
  normalized: string[]
  case_name: string
  clusters: number
  skipped_reason: string
}

type VerifyResponse = {
  ok: boolean
  citations: Citation[]
  stats: Record<string, number | string>
  message: string
}

type HealthResponse = {
  eyecite: boolean
  citation_lookup_url: string
  has_courtlistener_token: boolean
}

const SAMPLE = `My landlord won't return my security deposit. Under NY General Obligations Law, a landlord must return the deposit within 14 days. See also: Bush v. Gore, 531 U.S. 98 (2000).`

function statusLabel(c: Citation): { label: string; cls: string } {
  if (c.skipped_reason) return { label: "parsed (not verified)", cls: "border-slate-400/40 bg-slate-400/10 text-slate-300" }
  if (c.status === 404) return { label: "FABRICATED — not found", cls: "border-red-400/40 bg-red-400/10 text-red-300" }
  if (c.status === 300) return { label: "ambiguous", cls: "border-amber-400/40 bg-amber-400/10 text-amber-300" }
  if (c.status === 200) return { label: "verified", cls: "border-emerald-400/40 bg-emerald-400/10 text-emerald-300" }
  return { label: "unverified", cls: "border-white/20 bg-white/5 text-white/60" }
}

export default function RobsLawyerPage() {
  const backendUrl = useUiStore((state) => state.backendUrl)
  const [text, setText] = useState("")
  const [result, setResult] = useState<VerifyResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const healthQuery = useQuery({
    queryKey: ["legal-health", backendUrl],
    queryFn: async () => {
      const res = await fetch(`${backendUrl}/legal/health`, { headers: { Accept: "application/json" } })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      return (await res.json()) as HealthResponse
    },
    staleTime: 60_000,
  })

  async function runVerify() {
    if (!text.trim() || !backendUrl) return
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`${backendUrl}/legal/verify-citations`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ text }),
      })
      if (!res.ok) {
        const body = await res.text().catch(() => "")
        throw new Error(`HTTP ${res.status} ${res.statusText}${body ? ` — ${body.slice(0, 200)}` : ""}`)
      }
      setResult((await res.json()) as VerifyResponse)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  const health = healthQuery.data

  return (
    <div className="space-y-6 p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">Rob's Lawyer</h1>
          <p className="text-sm text-white/50">
            Citation verification for your own legal research. Not legal advice — always verify against the official source.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {healthQuery.isLoading && <Badge className="border-white/20 bg-white/5 text-white/60">checking…</Badge>}
          {health && (
            <Badge className={health.eyecite ? "border-emerald-400/40 bg-emerald-400/10 text-emerald-300" : "border-red-400/40 bg-red-400/10 text-red-300"}>
              eyecite {health.eyecite ? "ready" : "missing"}
            </Badge>
          )}
          {health && (
            <Badge className={health.has_courtlistener_token ? "border-emerald-400/40 bg-emerald-400/10 text-emerald-300" : "border-amber-400/40 bg-amber-400/10 text-amber-300"}>
              CourtListener {health.has_courtlistener_token ? "token set" : "no token (existence checks skipped)"}
            </Badge>
          )}
        </div>
      </div>

      <Card className="border-white/10 bg-panel">
        <CardHeader>
          <CardTitle>Verify citations</CardTitle>
          <CardDescription>
            Paste a legal passage, memo, or draft. Every case citation is parsed with Eyecite and checked against
            CourtListener's citation database. Fabricated citations are flagged red; statutes and id./supra are parsed
            but not externally verified.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <textarea
            className="w-full min-h-[160px] rounded-xl border border-white/10 bg-black/30 p-3 font-mono text-sm text-white/90 focus:outline-none focus:border-white/30"
            placeholder="Paste legal text here…"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <div className="flex flex-wrap gap-2">
            <Button onClick={runVerify} disabled={loading || !text.trim()}>
              {loading ? "Verifying…" : "Verify citations"}
            </Button>
            <Button variant="outline" onClick={() => setText(SAMPLE)}>
              Load sample
            </Button>
          </div>

          {error && <div className="rounded-xl border border-red-400/40 bg-red-400/10 p-3 text-sm text-red-200">{error}</div>}

          {result && (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <Badge className="border-white/20 bg-white/5 text-white/70">{result.stats.count} parsed</Badge>
                <Badge className="border-emerald-400/40 bg-emerald-400/10 text-emerald-300">{result.stats.verified} verified</Badge>
                {Number(result.stats.fabricated) > 0 && (
                  <Badge className="border-red-400/40 bg-red-400/10 text-red-300">{result.stats.fabricated} fabricated</Badge>
                )}
                {Number(result.stats.ambiguous) > 0 && (
                  <Badge className="border-amber-400/40 bg-amber-400/10 text-amber-300">{result.stats.ambiguous} ambiguous</Badge>
                )}
                <span className="text-white/50">{result.message}</span>
              </div>

              {result.citations.map((c, i) => {
                const s = statusLabel(c)
                return (
                  <div key={i} className="rounded-xl border border-white/10 bg-black/20 p-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex items-center gap-2 min-w-0">
                        <Badge className={s.cls}>{s.label}</Badge>
                        <Badge className="border-white/15 bg-white/5 text-white/50">{c.kind}</Badge>
                        <span className="font-mono text-sm text-white/90 truncate">{c.raw}</span>
                      </div>
                      {c.case_name && <span className="text-sm text-white/60 truncate">{c.case_name}</span>}
                    </div>
                    {c.normalized.length > 0 && (
                      <div className="mt-2 text-xs text-white/40">normalized: {c.normalized.join(", ")}</div>
                    )}
                    {c.error_message && <div className="mt-1 text-xs text-red-300/80">{c.error_message}</div>}
                    {c.skipped_reason && <div className="mt-1 text-xs text-white/40">{c.skipped_reason}</div>}
                  </div>
                )
              })}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
