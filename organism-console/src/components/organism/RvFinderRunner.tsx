import { useState } from "react"
import { safeExternalUrl } from "../../lib/utils"

interface Props {
  backendUrl: string
}

const RV_TYPES = [
  { value: "class b/c", label: "Class B + C (no Class A)" },
  { value: "class c", label: "Class C (motorized)" },
  { value: "class b", label: "Class B / camper van" },
  { value: "motorhome", label: "Any motorhome (A/B/C)" },
  { value: "fifth wheel", label: "Fifth wheel" },
  { value: "all", label: "All types" }
]

interface ListingAnalysis {
  score?: number
  verdict?: string
  fair_value_range?: string
  fair_value?: number
  pros?: string[]
  cons?: string[]
  red_flags?: string[]
  negotiation_tip?: string
  reasoning?: string
  engine?: string
  mpg?: string
  solar?: string
  weak_spots?: string[]
  livability?: string
  life_ease?: number
}

interface Listing {
  title?: string
  year?: number
  make?: string
  model?: string
  rv_type?: string
  price?: number
  url?: string
  location?: string
  mileage?: number
  size_ft?: number
  sleeps?: number
  distance_miles?: number
  description?: string
  analysis?: ListingAnalysis
}

interface SearchResponse {
  ok?: boolean
  budget?: number
  rv_type?: string
  location?: string
  radius_miles?: number
  elapsed_seconds?: number
  total_found?: number
  listings?: Listing[]
  top_pick?: Listing | null
  best_motorhome?: Listing | null
  summary?: string
  deep_dive?: string
  source_counts?: Record<string, number>
}

function distanceLabel(miles?: number): string {
  if (miles === undefined || miles === null) return "distance unknown"
  if (miles < 1) return "under 1 mi"
  return `${Math.round(miles)} mi`
}

function price(n?: number): string {
  if (typeof n !== "number" || !isFinite(n) || n <= 0) return "—"
  return `$${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}`
}

function scoreColor(score?: number): string {
  if (typeof score !== "number") return "rgba(255,255,255,0.5)"
  if (score >= 70) return "#34d399"
  if (score >= 55) return "#fbbf24"
  return "#f87171"
}

function chipList(items?: string[], color = "#22d3ee"): JSX.Element | null {
  if (!items || items.length === 0) return null
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
      {items.map((item, i) => (
        <span key={i} style={{ background: "rgba(0,0,0,0.3)", color, border: `1px solid ${color}44`, padding: "3px 9px", borderRadius: 99, fontSize: 11, fontWeight: 600 }}>{item}</span>
      ))}
    </div>
  )
}

function ListingCard({ listing, highlighted }: { listing: Listing; highlighted?: boolean }) {
  const a = listing.analysis ?? {}
  return (
    <div style={{
      padding: 16,
      borderRadius: 14,
      background: highlighted ? "rgba(34,211,238,0.06)" : "rgba(2,8,20,0.55)",
      border: highlighted ? "1px solid rgba(34,211,238,0.4)" : "1px solid rgba(255,255,255,0.08)"
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12 }}>
        <div>
          <div style={{ fontWeight: 800, color: "#fff", fontSize: 14 }}>
            {[listing.year, listing.make, listing.model, listing.title]
              .filter((v): v is string | number => typeof v === "number" ? v > 0 : !!v && v !== (listing as any).title)
              .filter((v, i, arr) => arr.indexOf(v) === i)
              .slice(0, 3).join(" ") || listing.title || "Unknown RV"}
          </div>
          <div style={{ fontSize: 11, color: "rgba(255,255,255,0.45)", marginTop: 3, textTransform: "uppercase", letterSpacing: "0.05em" }}>
            {[listing.rv_type, listing.location, listing.distance_miles !== undefined && listing.distance_miles !== null ? `📍 ${distanceLabel(listing.distance_miles)}` : "", listing.year ? `${listing.year}` : "", listing.mileage ? `${listing.mileage.toLocaleString()} mi` : "", listing.size_ft ? `${listing.size_ft} ft` : "", listing.sleeps ? `sleeps ${listing.sleeps}` : ""].filter(Boolean).join(" · ") || "—"}
          </div>
        </div>
        <div style={{ textAlign: "right", flexShrink: 0 }}>
          <div style={{ fontSize: 18, fontWeight: 800, color: "#fbbf24" }}>{price(listing.price)}</div>
          {a.score !== undefined && (
            <div style={{ fontSize: 13, fontWeight: 800, color: scoreColor(a.score) }}>
              Deal Score {a.score}
            </div>
          )}
        </div>
      </div>

      {a.verdict && (
        <div style={{ marginTop: 10, fontSize: 12, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.06em", color: scoreColor(a.score) }}>
          {a.verdict}
          {a.fair_value_range ? <span style={{ color: "rgba(255,255,255,0.45)", fontWeight: 600, textTransform: "none", letterSpacing: 0 }}> — fair value {a.fair_value_range}</span> : null}
        </div>
      )}

      {a.negotiation_tip && (
        <div style={{ marginTop: 8, fontSize: 12, color: "#fbbf24", lineHeight: 1.5 }}>
          💡 {a.negotiation_tip}
        </div>
      )}

      {(a.pros && a.pros.length > 0) && (
        <div style={{ marginTop: 10 }}>
          {chipList(a.pros, "#34d399")}
        </div>
      )}
      {(a.cons && a.cons.length > 0) && (
        <div style={{ marginTop: 6 }}>
          {chipList(a.cons, "#f87171")}
        </div>
      )}
      {(a.red_flags && a.red_flags.length > 0) && (
        <div style={{ marginTop: 8, fontSize: 12, color: "#f87171", fontWeight: 700 }}>
          ⚠️ Red flags: {a.red_flags.join(", ")}
        </div>
      )}

      {safeExternalUrl(listing.url) ? (
        <a href={safeExternalUrl(listing.url)} target="_blank" rel="noreferrer" style={{ display: "inline-block", marginTop: 10, fontSize: 12, color: "#22d3ee", textDecoration: "none", borderBottom: "1px solid #22d3ee55" }}>
          View listing ↗
        </a>
      ) : null}
    </div>
  )
}

export function RvFinderRunner({ backendUrl }: Props) {
  const [budget, setBudget] = useState(30000)
  const [rvType, setRvType] = useState("class b/c")
  const [location, setLocation] = useState("Roosevelt, NY 11575")
  const [radius, setRadius] = useState(50)
  const [deepDive, setDeepDive] = useState(false)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<SearchResponse | null>(null)

  async function run() {
    setRunning(true)
    setError(null)
    setResult(null)
    const start = Date.now()
    try {
      const res = await fetch(`${backendUrl}/features/rv-finder/search`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          budget: Math.max(1000, budget),
          rv_type: rvType,
          max_results: 15,
          deep_dive: deepDive ? 5 : 0,
          use_ppl: true,
          use_web: true,
          location: location.trim() || "Roosevelt, NY 11575",
          radius_miles: radius
        })
      })
      const data = await res.json()
      if (!res.ok || data?.ok === false) {
        throw new Error(data?.detail || data?.message || `Server returned status ${res.status}`)
      }
      setResult({ ...data, elapsed_seconds: data.elapsed_seconds ?? (Date.now() - start) / 1000 })
    } catch (e) {
      setError(String(e))
    } finally {
      setRunning(false)
    }
  }

  return (
    <div style={{ marginTop: 20, borderTop: "2px solid rgba(34,211,238,0.3)", paddingTop: 20 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14 }}>
        <span style={{ fontSize: 18 }}>🚐</span>
        <div>
          <div style={{ fontSize: 14, fontWeight: 800, color: "#22d3ee", textTransform: "uppercase", letterSpacing: "0.08em" }}>Run RV Finder</div>
          <div style={{ fontSize: 12, color: "rgba(255,255,255,0.5)", marginTop: 2 }}>Scans PPL + web for real listings under budget within your radius, and ranks the best deals.</div>
        </div>
      </div>

      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center", marginBottom: 12 }}>
        <label style={{ fontSize: 12, color: "rgba(255,255,255,0.6)", fontWeight: 700, display: "flex", flexDirection: "column", gap: 4 }}>
          Budget
          <input
            type="number"
            min={1000}
            step={500}
            value={budget}
            onChange={(e) => setBudget(Number(e.target.value))}
            style={{
              width: 110, background: "rgba(0,0,0,0.4)", border: "1px solid rgba(34,211,238,0.25)", borderRadius: 10,
              padding: "8px 10px", color: "white", fontSize: 14, outline: "none", fontFamily: "inherit"
            }}
          />
        </label>
        <label style={{ fontSize: 12, color: "rgba(255,255,255,0.6)", fontWeight: 700, display: "flex", flexDirection: "column", gap: 4 }}>
          RV type
          <select
            value={rvType}
            onChange={(e) => setRvType(e.target.value)}
            style={{
              width: 180, background: "rgba(0,0,0,0.4)", border: "1px solid rgba(34,211,238,0.25)", borderRadius: 10,
              padding: "8px 10px", color: "white", fontSize: 14, outline: "none", fontFamily: "inherit"
            }}
          >
            {RV_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
          </select>
        </label>
        <label style={{ fontSize: 12, color: "rgba(255,255,255,0.6)", fontWeight: 700, display: "flex", flexDirection: "column", gap: 4 }}>
          Location
          <input
            type="text"
            value={location}
            onChange={(e) => setLocation(e.target.value)}
            placeholder="Zip or City, ST"
            style={{
              width: 170, background: "rgba(0,0,0,0.4)", border: "1px solid rgba(34,211,238,0.25)", borderRadius: 10,
              padding: "8px 10px", color: "white", fontSize: 14, outline: "none", fontFamily: "inherit"
            }}
          />
        </label>
        <label style={{ fontSize: 12, color: "rgba(255,255,255,0.6)", fontWeight: 700, display: "flex", flexDirection: "column", gap: 4 }}>
          Radius
          <select
            value={radius}
            onChange={(e) => setRadius(Number(e.target.value))}
            style={{
              width: 110, background: "rgba(0,0,0,0.4)", border: "1px solid rgba(34,211,238,0.25)", borderRadius: 10,
              padding: "8px 10px", color: "white", fontSize: 14, outline: "none", fontFamily: "inherit"
            }}
          >
            <option value={25}>25 mi</option>
            <option value={50}>50 mi</option>
            <option value={100}>100 mi</option>
            <option value={200}>200 mi</option>
            <option value={0}>Anywhere</option>
          </select>
        </label>
        <label style={{ fontSize: 12, color: "rgba(255,255,255,0.6)", fontWeight: 700, display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
          <input type="checkbox" checked={deepDive} onChange={(e) => setDeepDive(e.target.checked)} style={{ accentColor: "#22d3ee", width: 16, height: 16 }} />
          Deep-dive report ({deepDive ? "adds ~30s" : "fast"})
        </label>
      </div>

      <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 14 }}>
        <button
          onClick={run}
          disabled={running}
          style={{
            padding: "11px 28px", borderRadius: 14, border: "none",
            background: running ? "rgba(255,255,255,0.08)" : "linear-gradient(135deg,#0891b2,#22d3ee)",
            color: running ? "rgba(255,255,255,0.4)" : "#000", fontWeight: 800, fontSize: 14,
            cursor: running ? "not-allowed" : "pointer", transition: "all 0.15s", display: "flex", alignItems: "center", gap: 8
          }}
        >
          {running ? (
            <>
              <span style={{ display: "inline-block", animation: "spin 1s linear infinite" }}>⏳</span>
              Searching RVs...
            </>
          ) : `▶ Find best ${rvType === "all" ? "RV" : rvType} within ${radius > 0 ? `${radius} mi of ${location}` : "anywhere"} under $${budget.toLocaleString()}`}
        </button>
      </div>

      {error && (
        <div style={{ padding: 12, background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.3)", borderRadius: 10, color: "#f87171", fontSize: 13 }}>
          <strong>Error:</strong> {error}
        </div>
      )}

      {result && (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          {result.summary && (
            <div style={{ padding: 14, background: "rgba(0,0,0,0.3)", border: "1px solid rgba(255,255,255,0.08)", borderRadius: 12, fontSize: 13, lineHeight: 1.6, color: "rgba(225,245,255,0.9)" }}>
              {result.summary}
              <div style={{ marginTop: 8, fontSize: 11, color: "rgba(255,255,255,0.4)" }}>
                {result.total_found} matched{result.location && (result.radius_miles ?? 0) > 0 ? ` · within ${result.radius_miles} mi of ${result.location}` : ""} · {result.elapsed_seconds}s · sources: {JSON.stringify(result.source_counts ?? {})}
              </div>
            </div>
          )}

          {result.top_pick && (
            <div>
              <div style={{ fontSize: 11, fontWeight: 800, color: "#22d3ee", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 8 }}>🏆 Top pick</div>
              <ListingCard listing={result.top_pick} highlighted />
            </div>
          )}

          {result.best_motorhome && (
            <div>
              <div style={{ fontSize: 11, fontWeight: 800, color: "#34d399", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 8 }}>🚐 Best motorhome</div>
              <ListingCard listing={result.best_motorhome} />
            </div>
          )}

          {(result.listings ?? []).length > 0 && (
            <div>
              <div style={{ fontSize: 11, fontWeight: 800, color: "rgba(255,255,255,0.55)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 8 }}>All matches</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                {(result.listings ?? []).map((l, i) => <ListingCard key={i} listing={l} />)}
              </div>
            </div>
          )}

          {result.deep_dive && (
            <div style={{ padding: 16, background: "rgba(0,0,0,0.35)", border: "1px solid rgba(251,191,36,0.25)", borderRadius: 14 }}>
              <div style={{ fontSize: 11, fontWeight: 800, color: "#fbbf24", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 10 }}>📊 Deep-dive analysis</div>
              <pre style={{ whiteSpace: "pre-wrap", fontFamily: "inherit", color: "rgba(225,245,255,0.9)", fontSize: 13, lineHeight: 1.75, margin: 0, maxHeight: 420, overflowY: "auto" }}>{result.deep_dive}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
