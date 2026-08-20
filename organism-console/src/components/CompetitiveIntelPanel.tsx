import { useState } from "react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card"
import { Button } from "./ui/button"
import { Badge } from "./ui/badge"

type Competitor = {
  id: string
  name: string
  url: string
  tier: "top_3" | "tier_2"
  targets: string[]
  enabled: boolean
}

type IntelChange = {
  id: string
  competitor: string
  kind: string
  classification: string
  significance: number
  url: string
  snippet: string
  what_changed: string
  so_what: string
}

type Digest = {
  id: string
  generated_at: string
  item_count: number
  items: IntelChange[]
  provider: string
}

type Props = { backendUrl: string }

const TIER_COLORS: Record<string, string> = {
  top_3: "border-red-400/40 bg-red-400/10 text-red-300",
  tier_2: "border-white/20 bg-white/5 text-white/50",
}

const SIG_COLORS: Record<number, string> = {
  5: "border-red-400/50 bg-red-400/10 text-red-300",
  4: "border-amber-400/50 bg-amber-400/10 text-amber-300",
  3: "border-yellow-400/40 bg-yellow-400/10 text-yellow-200",
  2: "border-emerald-400/40 bg-emerald-400/10 text-emerald-300",
  1: "border-white/20 bg-white/5 text-white/50",
}

export default function CompetitiveIntelPanel({ backendUrl }: Props) {
  const [competitors, setCompetitors] = useState<Competitor[]>([])
  const [name, setName] = useState("")
  const [url, setUrl] = useState("")
  const [tier, setTier] = useState<"top_3" | "tier_2">("tier_2")
  const [changes, setChanges] = useState<IntelChange[]>([])
  const [digests, setDigests] = useState<Digest[]>([])
  const [msg, setMsg] = useState("")
  const [busy, setBusy] = useState(false)

  const loadCompetitors = async () => {
    const r = await (await fetch(`${backendUrl}/features/intel/competitors`, { headers: { Accept: "application/json" } })).json()
    if (r.ok) setCompetitors(r.competitors ?? [])
  }

  const loadChanges = async () => {
    const r = await (await fetch(`${backendUrl}/features/intel/changes?limit=15`, { headers: { Accept: "application/json" } })).json()
    if (r.ok) setChanges(r.changes ?? [])
  }

  const loadDigests = async () => {
    const r = await (await fetch(`${backendUrl}/features/intel/history?limit=5`, { headers: { Accept: "application/json" } })).json()
    if (r.ok) setDigests(r.digests ?? [])
  }

  const init = () => {
    loadCompetitors()
    loadChanges()
    loadDigests()
  }

  const addCompetitor = async () => {
    if (!name.trim() || !url.trim()) return
    setBusy(true)
    try {
      const r = await (await fetch(`${backendUrl}/features/intel/competitors`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim(), url: url.trim(), tier }),
      })).json()
      setMsg(r.ok ? `✓ Added ${name.trim()}` : `✗ ${r.error}`)
      if (r.ok) { setName(""); setUrl(""); loadCompetitors() }
    } finally { setBusy(false) }
  }

  const removeCompetitor = async (id: string) => {
    const r = await (await fetch(`${backendUrl}/features/intel/competitors/${id}`, { method: "DELETE" })).json()
    setMsg(r.ok ? "Removed." : `✗ ${r.error}`)
    loadCompetitors()
  }

  const toggleCompetitor = async (c: Competitor) => {
    const r = await (await fetch(`${backendUrl}/features/intel/competitors/${c.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: !c.enabled }),
    })).json()
    if (r.ok) loadCompetitors()
  }

  const runScan = async () => {
    setBusy(true); setMsg("Scanning all competitors…")
    try {
      const r = await (await fetch(`${backendUrl}/features/intel/scan`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}),
      })).json()
      setMsg(r.changed ? `✓ Scan: ${r.changed} change(s) detected (${r.scanned} competitors).` : `Scan: no changes detected (${r.scanned} competitors).`)
      loadChanges()
    } finally { setBusy(false) }
  }

  const runDigest = async () => {
    setBusy(true); setMsg("Building digest…")
    try {
      const r = await (await fetch(`${backendUrl}/features/intel/digest?cap=15`, { method: "POST" })).json()
      setMsg(r.id ? `✓ Digest ${r.id.slice(0, 8)}: ${r.item_count} curated items.` : `Digest failed: ${r.error ?? "unknown"}`)
      loadDigests()
    } finally { setBusy(false) }
  }

  const runFull = async () => {
    setBusy(true); setMsg("Full run: scan → digest → deliver…")
    try {
      const r = await (await fetch(`${backendUrl}/features/intel/run`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}),
      })).json()
      setMsg(r.changed ? `✓ Run complete: ${r.changed} changes, ${r.item_count} digest items.` : `Run: no changes detected.`)
      loadChanges(); loadDigests()
    } finally { setBusy(false) }
  }

  return (
    <Card className="border-white/10 bg-panel xl:col-span-2">
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <div>
          <CardTitle>Competitive Intel — the paid monitoring service</CardTitle>
          <CardDescription>
            Monitor competitors → deterministic change detection → curated "so what" digest. The change detector never consults an LLM; the interpretation is the only AI seam.
          </CardDescription>
        </div>
        <div className="flex gap-2">
          <Button size="sm" variant="outline" onClick={init}>Refresh</Button>
          <Button size="sm" variant="outline" disabled={busy} onClick={runScan}>{busy ? "Working…" : "Scan"}</Button>
          <Button size="sm" variant="outline" disabled={busy} onClick={runDigest}>Digest</Button>
          <Button size="sm" disabled={busy} onClick={runFull}>{busy ? "Working…" : "Full run"}</Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Competitor registry */}
        <div>
          <div className="mb-2 flex items-center justify-between">
            <div className="text-xs font-semibold uppercase tracking-wide text-white/40">Competitors ({competitors.length})</div>
          </div>
          <div className="flex gap-2">
            <input
              className="w-36 rounded-md border border-white/10 bg-black/40 px-3 py-1.5 text-sm text-white/80"
              placeholder="name"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <input
              className="flex-1 rounded-md border border-white/10 bg-black/40 px-3 py-1.5 text-sm text-white/80"
              placeholder="https://…"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && addCompetitor()}
            />
            <select
              className="rounded-md border border-white/10 bg-black/40 px-2 py-1.5 text-xs text-white/80"
              value={tier}
              onChange={(e) => setTier(e.target.value as "top_3" | "tier_2")}
            >
              <option value="top_3">top 3</option>
              <option value="tier_2">tier 2</option>
            </select>
            <Button size="sm" variant="outline" disabled={!name.trim() || !url.trim() || busy} onClick={addCompetitor}>Add</Button>
          </div>
          {competitors.length > 0 && (
            <div className="mt-2 space-y-1.5">
              {competitors.map((c) => (
                <div key={c.id} className="flex items-center justify-between gap-3 rounded-xl border border-white/10 bg-black/20 px-3 py-2">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-semibold truncate">{c.name}</span>
                      <Badge className={TIER_COLORS[c.tier] ?? ""}>{c.tier}</Badge>
                      {!c.enabled && <Badge variant="outline">paused</Badge>}
                    </div>
                    <div className="truncate text-xs text-white/40">{c.url} · {c.targets.join(", ")}</div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1.5">
                    <Button size="sm" variant="outline" onClick={() => toggleCompetitor(c)}>{c.enabled ? "Pause" : "Resume"}</Button>
                    <Button size="sm" variant="ghost" className="text-red-300 hover:text-red-200" onClick={() => removeCompetitor(c.id)}>✕</Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {msg && <div className="text-xs text-white/60">{msg}</div>}

        {/* Latest changes */}
        {changes.length > 0 && (
          <div>
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-white/40">Latest changes</div>
            <div className="space-y-1.5">
              {changes.map((c) => (
                <div key={c.id} className="rounded-xl border border-white/10 bg-black/20 p-3">
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2 min-w-0">
                      <span className="font-semibold truncate">{c.competitor}</span>
                      <span className="text-xs text-white/40">{c.kind}</span>
                      <Badge variant="outline" className="capitalize">{c.classification}</Badge>
                      <Badge className={SIG_COLORS[c.significance] ?? ""}>sig {c.significance}</Badge>
                    </div>
                  </div>
                  <div className="mt-1 text-xs text-white/70">{c.what_changed}</div>
                  <div className="mt-1 text-xs text-emerald-200/80">{c.so_what}</div>
                  {c.url && <a href={c.url} target="_blank" rel="noreferrer" className="mt-1 block truncate text-xs text-blue-300/70">{c.url}</a>}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Digest history */}
        {digests.length > 0 && (
          <div>
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-white/40">Digest history</div>
            <div className="space-y-1.5">
              {digests.map((d) => (
                <div key={d.id} className="rounded-xl border border-white/10 bg-black/20 p-3">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-xs">{d.id.slice(0, 8)}</span>
                      <span className="text-xs text-white/40">{new Date(d.generated_at).toLocaleString()}</span>
                      <Badge variant="outline">{d.item_count} items</Badge>
                      <Badge className="border-white/20 bg-white/5 text-white/50">{d.provider}</Badge>
                    </div>
                  </div>
                  <div className="mt-1 space-y-0.5 text-xs">
                    {(d.items ?? []).slice(0, 5).map((it, i) => (
                      <div key={i} className="text-white/60">
                        • <span className="text-white/80">{it.competitor}</span> ({it.classification}) — {it.so_what}
                      </div>
                    ))}
                    {(d.item_count ?? 0) > 5 && <div className="text-white/40">… +{(d.item_count ?? 0) - 5} more</div>}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
