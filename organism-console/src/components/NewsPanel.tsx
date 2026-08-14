import { useState } from "react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card"
import { Button } from "./ui/button"
import { Badge } from "./ui/badge"

type Subscriptions = { ok: boolean; topics: Record<string, string[]>; error?: string }

type Props = { backendUrl: string }

export default function NewsPanel({ backendUrl }: Props) {
  const [subs, setSubs] = useState<Subscriptions | null>(null)
  const [topic, setTopic] = useState("ai-agents")
  const [url, setUrl] = useState("")
  const [digest, setDigest] = useState("")
  const [digestLoading, setDigestLoading] = useState(false)
  const [ingestMsg, setIngestMsg] = useState("")
  const [showSubs, setShowSubs] = useState(false)

  const loadSubs = async () => {
    const r = await (await fetch(`${backendUrl}/features/news/subscriptions`, {
      headers: { Accept: "application/json" },
    })).json()
    setSubs(r)
  }

  const ingest = async () => {
    setIngestMsg("Fetching feeds…")
    const r = await (await fetch(`${backendUrl}/features/news/ingest?limit_per_feed=10`, {
      method: "POST",
    })).json()
    setIngestMsg(r.ingested ? `✓ Ingested ${r.ingested} new items.` : `No new items.${r.errors?.length ? ` ${r.errors.length} feed error(s).` : ""}`)
    setDigest("")
  }

  const runDigest = async () => {
    setDigestLoading(true)
    setDigest("")
    try {
      const r = await (await fetch(`${backendUrl}/features/news/digest`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ max_items: 30 }),
      })).json()
      setDigest(r.digest ?? r.error ?? "no digest")
    } catch (e: any) {
      setDigest(`Failed: ${e.message}`)
    } finally {
      setDigestLoading(false)
    }
  }

  const addSub = async () => {
    if (!url.trim()) return
    const r = await (await fetch(`${backendUrl}/features/news/subscriptions/add`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ topic, url }),
    })).json()
    setIngestMsg(r.ok ? `✓ Subscribed ${url} under "${topic}".` : `✗ ${r.error}`)
    if (r.ok) { setUrl(""); loadSubs() }
  }

  return (
    <Card className="border-white/10 bg-panel xl:col-span-2">
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <div>
          <CardTitle>News Digest — custom topics, follow evolving stories</CardTitle>
          <CardDescription>
            RSS/Atom feeds per topic → ingest what's new → LLM digest grouped by topic with evolving-story flags. Spark/Perplexity "custom news digest" parity, self-hosted.
          </CardDescription>
        </div>
        <div className="flex gap-2">
          <Button size="sm" variant="outline" onClick={loadSubs}>{showSubs ? "Hide feeds" : "Feeds"}</Button>
          <Button size="sm" variant="outline" onClick={ingest}>Ingest feeds</Button>
          <Button size="sm" disabled={digestLoading} onClick={runDigest}>{digestLoading ? "Digesting…" : "Digest"}</Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {subs && showSubs && (
          <div className="rounded-xl border border-white/10 bg-black/20 p-3">
            <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-white/40">Subscribed feeds</div>
            <ul className="space-y-1 text-xs">
              {Object.entries(subs.topics ?? {}).map(([t, urls]) => (
                <li key={t}>
                  <span className="font-semibold text-emerald-300">{t}:</span>{" "}
                  <span className="text-white/60">{urls.join(", ")}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="flex gap-2">
          <input
            className="w-36 rounded-md border border-white/10 bg-black/40 px-3 py-1.5 text-sm text-white/80"
            placeholder="topic"
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
          />
          <input
            className="flex-1 rounded-md border border-white/10 bg-black/40 px-3 py-1.5 text-sm text-white/80"
            placeholder="feed url (https://…, allowlisted hosts)"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && addSub()}
          />
          <Button size="sm" variant="outline" disabled={!url.trim()} onClick={addSub}>Subscribe</Button>
        </div>

        {ingestMsg && <div className="text-xs text-white/50">{ingestMsg}</div>}

        {digest && (
          <div className="rounded-xl border border-emerald-400/20 bg-emerald-400/5 p-3">
            <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-white/40">Today's digest</div>
            <pre className="whitespace-pre-wrap text-sm text-emerald-200/90">{digest}</pre>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
