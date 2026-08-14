import { useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { useUiStore } from "../state/ui-store"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/card"
import { Badge } from "../components/ui/badge"
import { Button } from "../components/ui/button"

type Book = {
  slug: string
  title: string
  author: string
  track: string
  track_label: string
  priority: string
  scores: Record<string, number>
  legitimate_source: string
  public_domain: boolean
  summary_status: string
  ai_relevance: string
  best_parts: string[]
  warnings: string[]
  freelancer_translation: string
}

type Library = {
  ok: boolean
  count: number
  books: Book[]
  tracks: string[]
  priorities: string[]
}

type SearchResult = { slug: string; title: string; author: string; track: string; priority: string; ai_relevance: string; score: number }
type SynthesizeOutput = {
  ok: boolean
  question: string
  topic_tracks: string[]
  fragments: Book[]
}

const PRIORITY_COLOR: Record<string, string> = {
  "READ NOW": "border-emerald-400/50 bg-emerald-400/10 text-emerald-300",
  "READ LATER": "border-sky-400/50 bg-sky-400/10 text-sky-300",
  REFERENCE: "border-white/20 bg-white/5 text-white/60",
}

const TRACK_COLOR: Record<string, string> = {
  income: "text-amber-300",
  mindset: "text-purple-300",
  "personal finance": "text-sky-300",
  investing: "text-teal-300",
  "real estate": "text-orange-300",
  technical: "text-emerald-300",
}

export default function BooksPage() {
  const backendUrl = useUiStore((state) => state.backendUrl)
  const [track, setTrack] = useState<string>("all")
  const [priority, setPriority] = useState<string>("all")
  const [query, setQuery] = useState("")
  const [selected, setSelected] = useState<string | null>(null)
  const [ask, setAsk] = useState("")
  const [synth, setSynth] = useState<SynthesizeOutput | null>(null)
  const [asking, setAsking] = useState(false)

  const library = useQuery({
    queryKey: ["books", track, priority, backendUrl],
    queryFn: async () => {
      const params = new URLSearchParams()
      if (track !== "all") params.set("track", track)
      if (priority !== "all") params.set("priority", priority)
      const res = await fetch(`${backendUrl}/books?${params.toString()}`, { headers: { Accept: "application/json" } })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      return (await res.json()) as Library
    },
    staleTime: 60_000,
    retry: 1,
  })

  const search = useQuery({
    queryKey: ["books-search", query, backendUrl],
    queryFn: async () => {
      const res = await fetch(`${backendUrl}/books/search?q=${encodeURIComponent(query)}`, { headers: { Accept: "application/json" } })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const j = (await res.json()) as { ok: boolean; results: SearchResult[] }
      return j.results
    },
    enabled: query.trim().length >= 2,
    staleTime: 30_000,
  })

  const detail = useQuery({
    queryKey: ["book", selected, backendUrl],
    queryFn: async () => {
      const res = await fetch(`${backendUrl}/books/${selected}`, { headers: { Accept: "application/json" } })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const j = (await res.json()) as { ok: boolean; book: Book }
      return j.book
    },
    enabled: !!selected,
    staleTime: Infinity,
  })

  const tracks = library.data?.tracks ?? ["all", "income", "mindset", "personal finance", "investing", "real estate", "technical"]
  const priorities = library.data?.priorities ?? ["all", "READ NOW", "READ LATER", "REFERENCE"]

  const askLibrary = async () => {
    if (ask.trim().length < 3) return
    setAsking(true)
    try {
      const res = await fetch(`${backendUrl}/books/synthesize`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ question: ask }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setSynth((await res.json()) as SynthesizeOutput)
    } catch {
      setSynth({ ok: false, question: ask, topic_tracks: [], fragments: [] })
    } finally {
      setAsking(false)
    }
  }

  const visibleBooks = useMemo(() => {
    if (query.trim().length >= 2 && search.data) {
      const slugs = new Map(search.data.map((r) => [r.slug, r]))
      return library.data?.books.filter((b) => slugs.has(b.slug)) ?? []
    }
    return library.data?.books ?? []
  }, [query, search.data, library.data])

  return (
    <div className="space-y-6 p-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">Book Library</h1>
          <p className="max-w-3xl text-sm text-white/60">
            Your AI/Data freelancer knowledge base: expert digests of the career bookshelf. Original synthesis about each
            book - never the full text. Use library access or summaries for the copyrighted originals.
          </p>
        </div>
        {library.data && (
          <div className="flex flex-wrap gap-2">
            <Badge className="border-white/20 bg-white/5 text-white/70">{library.data.count} books</Badge>
          </div>
        )}
      </header>

      {library.isError && (
        <Card className="border-amber-400/40 bg-amber-400/5">
          <CardContent className="py-4 text-sm text-amber-200">
            Books manifest not loaded. Run{" "}
            <code className="rounded bg-black/40 px-1.5 py-0.5">.venv\Scripts\python.exe scripts\build_book_manifest.py</code>{" "}
            and restart the backend.
          </CardContent>
        </Card>
      )}

      <Card className="border-white/10 bg-panel">
        <CardHeader>
          <CardTitle className="text-base">Ask the library</CardTitle>
          <CardDescription>
            Cross-book reasoning: e.g. "Combine E-Myth + Lean Startup + Zero to One into a strategy for productizing my AI automation service."
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="flex gap-2">
            <input
              value={ask}
              onChange={(e) => setAsk(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && askLibrary()}
              placeholder="Ask a question across all books..."
              className="flex-1 rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm text-white outline-none focus:border-emerald-400/50"
            />
            <Button onClick={askLibrary} disabled={asking || ask.trim().length < 3}>
              {asking ? "…" : "Synthesize"}
            </Button>
          </div>
          <div className="text-xs text-white/40">Matches the topic first, then surfaces the highest-signal digest fragments for you (or an agent) to reason over.</div>
        </CardContent>
      </Card>

      {synth && (
        <Card className="border-emerald-400/30 bg-emerald-950/10">
          <CardContent className="py-4">
            <div className="mb-2 text-sm font-semibold text-emerald-300">Grounded fragments · question: {synth.question}</div>
            {synth.topic_tracks.length > 0 && (
              <div className="mb-3 flex flex-wrap gap-1.5">
                {synth.topic_tracks.map((t) => (
                  <Badge key={t} className={`border-white/20 bg-white/5 ${TRACK_COLOR[t] ?? "text-white/70"}`}>{t}</Badge>
                ))}
              </div>
            )}
            <div className="space-y-3">
              {synth.fragments.map((f) => (
                <div key={f.slug} className="rounded-lg border border-white/10 bg-black/20 p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      onClick={() => { setSelected(f.slug); setPriority(""); setTrack("") }}
                      className="text-sm font-semibold text-white hover:text-emerald-300"
                    >
                      {f.title}
                    </button>
                    <Badge className={PRIORITY_COLOR[f.priority]}>{f.priority}</Badge>
                  </div>
                  <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-white/70">
                    {f.best_parts.slice(0, 3).map((p, i) => <li key={i}>{p}</li>)}
                  </ul>
                  {f.freelancer_translation && (
                    <div className="mt-2 rounded bg-emerald-950/30 px-2 py-1 text-sm text-emerald-200/90">{f.freelancer_translation}</div>
                  )}
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      <div className="flex flex-wrap gap-2">
        {tracks.map((t) => (
          <button
            key={t}
            onClick={() => setTrack(t)}
            className={`rounded-lg border px-3 py-1.5 text-sm transition-colors ${track === t
              ? "border-emerald-400/50 bg-emerald-400/10 text-emerald-200"
              : "border-white/10 bg-white/5 text-white/70 hover:bg-white/10"}`}
          >
            {t === "all" ? "All" : t}
          </button>
        ))}
      </div>
      <div className="flex flex-wrap gap-2">
        {priorities.map((p) => (
          <button
            key={p}
            onClick={() => setPriority(p)}
            className={`rounded-lg border px-3 py-1.5 text-sm transition-colors ${priority === p
              ? "border-emerald-400/50 bg-emerald-400/10 text-emerald-200"
              : "border-white/10 bg-white/5 text-white/70 hover:bg-white/10"}`}
          >
            {p === "all" ? "Any priority" : p}
          </button>
        ))}
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search books..."
          className="ml-auto w-56 rounded-lg border border-white/10 bg-black/30 px-3 py-1.5 text-sm text-white outline-none focus:border-emerald-400/50"
        />
      </div>

      {library.isLoading && <Loading />}

      {visibleBooks.map((b) => (
        <Card key={b.slug} className={`border-white/10 ${selected === b.slug ? "bg-emerald-950/20" : "bg-panel"}`}>
          <CardHeader className="cursor-pointer" onClick={() => setSelected(selected === b.slug ? null : b.slug)}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap items-center gap-2">
                <CardTitle className="text-lg">{b.title}</CardTitle>
                <Badge className={TRACK_COLOR[b.track] ?? ""}>{b.track}</Badge>
                <Badge className={PRIORITY_COLOR[b.priority]}>{b.priority}</Badge>
              </div>
              <div className="flex flex-wrap gap-1">
                {Object.entries(b.scores ?? {})
                  .sort(([a], [c]) => a.localeCompare(c))
                  .map(([k, v]) => (
                    <Badge key={k} className="border-white/10 bg-white/5 text-white/50">{k}:{v}</Badge>
                  ))}
              </div>
            </div>
            <CardDescription>
              {b.author} · {b.track_label}
              {b.public_domain ? " · public domain" : " · library/summary"}
              {b.ai_relevance === "high" && " · ★ high AI relevance"}
            </CardDescription>
          </CardHeader>
          {selected === b.slug && detail.data?.slug === b.slug && (
            <CardContent className="space-y-3 border-t border-white/10 pt-4">
              <div>
                <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-white/50">Best parts to remember</div>
                <ul className="list-disc space-y-1 pl-5 text-sm text-white/80">
                  {detail.data.best_parts.map((p, i) => <li key={i}>{p}</li>)}
                </ul>
              </div>
              {detail.data.freelancer_translation && (
                <div>
                  <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-white/50">For you (AI/Data freelancer)</div>
                  <div className="rounded bg-emerald-950/30 px-3 py-2 text-sm text-emerald-200/90">{detail.data.freelancer_translation}</div>
                </div>
              )}
              {detail.data.warnings.length > 0 && (
                <div>
                  <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-amber-300/80">What to be cautious about</div>
                  <ul className="list-disc space-y-1 pl-5 text-sm text-amber-200/80">
                    {detail.data.warnings.map((w, i) => <li key={i}>{w}</li>)}
                  </ul>
                </div>
              )}
              <div className="text-xs text-white/40">
                Legitimate access: <span className="text-white/70">{detail.data.legitimate_source}</span>. This digest is original
                synthesis, not book text — for the full book use your library.
              </div>
            </CardContent>
          )}
          {selected === b.slug && !detail.data && <Loading />}
        </Card>
      ))}

      {visibleBooks.length === 0 && !library.isLoading && (
        <div className="text-sm text-white/40">No books match the current filters.</div>
      )}
    </div>
  )
}

function Loading() {
  return <div className="text-sm text-white/50">Loading the library.</div>
}