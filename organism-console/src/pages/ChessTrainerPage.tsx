import { useCallback, useEffect, useMemo, useState } from "react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/card"
import { Button } from "../components/ui/button"
import { Badge } from "../components/ui/badge"

const FILES = "abcdefgh"
const RANKS = "87654321"

// Unicode chess pieces (filled for white, outline for black via CSS).
const PIECE_GLYPHS: Record<string, string> = {
  K: "♔", Q: "♕", R: "♖", B: "♗", N: "♘", P: "♙",
  k: "♚", q: "♛", r: "♜", b: "♝", n: "♞", p: "♟",
}

const CLASS_COLOR: Record<string, string> = {
  Best: "border-emerald-400/60 bg-emerald-400/10 text-emerald-300",
  Excellent: "border-emerald-400/40 bg-emerald-400/5 text-emerald-300",
  Good: "border-sky-400/40 bg-sky-400/5 text-sky-300",
  Inaccuracy: "border-amber-400/40 bg-amber-400/5 text-amber-300",
  Mistake: "border-orange-400/40 bg-orange-400/5 text-orange-300",
  Blunder: "border-red-400/60 bg-red-400/10 text-red-300",
}

type EvalResult = {
  ok: boolean
  legal?: boolean
  san?: string
  classification?: string
  win_before_pct?: number
  win_after_pct?: number
  win_delta_pct?: number
  best_move_san?: string | null
  is_checkmate?: boolean
  is_stalemate?: boolean
  explanation?: string
  fen?: string
  error?: string
  legal_moves?: string[]
}

type EngineReply = {
  ok: boolean
  uci?: string
  san?: string
  fen?: string
  is_checkmate?: boolean
  is_stalemate?: boolean
  in_check?: boolean
  game_over?: boolean
  result?: string
  error?: string
}

type PracticePosition = { slug: string; name: string; goal: string; fen: string; tier: number }

function parseFen(fen: string): Record<string, string> {
  const board: Record<string, string> = {}
  const placement = fen.split(" ")[0]
  const rows = placement.split("/")
  for (let r = 0; r < 8; r++) {
    let file = 0
    for (const ch of rows[r]) {
      if (/\d/.test(ch)) { file += parseInt(ch); continue }
      if (file < 8) {
        const sq = FILES[file] + RANKS[r]
        board[sq] = ch
        file++
      }
    }
  }
  return board
}

function sideToMove(fen: string): "w" | "b" {
  return (fen.split(" ")[1] || "w") as "w" | "b"
}

export default function ChessTrainerPage() {
  const backendUrl = "http://127.0.0.1:8000"
  const [fen, setFen] = useState("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
  const [history, setHistory] = useState<string[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [lastMove, setLastMove] = useState<{ from: string; to: string } | null>(null)
  const [result, setResult] = useState<EvalResult | null>(null)
  const [evaluating, setEvaluating] = useState(false)
  const [practice, setPractice] = useState<PracticePosition[]>([])
  const [rating, setRating] = useState(500)
  const [level, setLevel] = useState(1)
  const [health, setHealth] = useState<{ engine?: { available: boolean }; book_index?: { available: boolean } } | null>(null)
  const [explanationOpen, setExplanationOpen] = useState(true)

  const board = useMemo(() => parseFen(fen), [fen])
  const turn = useMemo(() => sideToMove(fen), [fen])

  const refreshHealth = useCallback(async () => {
    try {
      const h = await (await fetch(`${backendUrl}/chess/trainer/health`)).json()
      setHealth(h)
    } catch { setHealth(null) }
  }, [backendUrl])

  useEffect(() => { refreshHealth() }, [refreshHealth])

  useEffect(() => {
    (async () => {
      try {
        const p = await (await fetch(`${backendUrl}/chess/trainer/practice`)).json()
        setPractice(p.positions ?? [])
      } catch { /* ignore */ }
    })()
  }, [backendUrl])

  // legal-move helper: the backend only validates; the client computes legal
  // targets by asking the backend for legality per candidate would be too slow,
  // so we rely on the trainer's legal-move list from the last non-legal attempt
  // and otherwise highlight the selected piece's moves heuristically.
  const onSquareClick = (sq: string) => {
    const piece = board[sq]
    if (piece) {
      const isMine = turn === "w" ? piece === piece.toUpperCase() : piece === piece.toLowerCase()
      if (isMine) {
        // Selecting own piece.
        if (selected === sq) { setSelected(null); return }
        setSelected(sq)
        return
      }
    }
    // A target square (empty or enemy piece) — attempt the move from selection.
    if (selected) {
      const uci = selected + sq
      void evaluateMove(uci)
      setSelected(null)
    }
  }

  const evaluateMove = async (uci: string) => {
    setEvaluating(true)
    setResult(null)
    try {
      const res = await (await fetch(`${backendUrl}/chess/trainer/evaluate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fen, uci, rating, want_explain: true }),
      })).json() as EvalResult
      setResult(res)
      setExplanationOpen(true)
      if (res.ok && res.legal && res.fen) {
        setHistory((h) => [...h, res.san ?? uci])
        setLastMove({ from: uci.slice(0, 2), to: uci.slice(2, 4) })
        setFen(res.fen)
        // After the player's move, let the engine reply.
        void engineReply(res.fen)
      }
    } catch {
      setResult({ ok: false, error: "evaluation failed" })
    } finally {
      setEvaluating(false)
    }
  }

  const engineReply = async (playerFen: string) => {
    try {
      const res = await (await fetch(`${backendUrl}/chess/trainer/engine-move`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fen: playerFen, rating, level }),
      })).json() as EngineReply
      if (res.ok && res.fen) {
        setHistory((h) => [...h, res.san ?? res.uci ?? ""])
        if (res.uci) setLastMove({ from: res.uci.slice(0, 2), to: res.uci.slice(2, 4) })
        setFen(res.fen)
      }
    } catch { /* ignore */ }
  }

  const resetToPractice = (p: PracticePosition) => {
    setFen(p.fen)
    setHistory([])
    setResult(null)
    setLastMove(null)
    setSelected(null)
    setExplanationOpen(true)
  }

  const undoMove = () => {
    setHistory((h) => h.slice(0, -1))
    setFen("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
    setResult(null)
    setLastMove(null)
  }

  const newGame = () => resetToPractice({ slug: "start", name: "Starting position", goal: "Play the opening", fen: "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", tier: 1 })

  return (
    <div className="space-y-6 p-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">Chess Trainer</h1>
          <p className="max-w-3xl text-sm text-white/60">
            Play on the board — Stockfish 18 evaluates every move and tells you WHY it's good or bad,
            grounded in your chess book library. Local model, engine, and books all run on this machine.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {health?.engine?.available && <Badge className="border-emerald-400/40 bg-emerald-400/10 text-emerald-300">Stockfish 18 ✓</Badge>}
          {health?.book_index?.available && <Badge className="border-sky-400/40 bg-sky-400/10 text-sky-300">Book index ✓</Badge>}
          <Badge className={turn === "w" ? "border-white/20 bg-white/5 text-white/70" : "border-white/20 bg-white/5 text-white/70"}>
            {turn === "w" ? "White to move" : "Black to move"}
          </Badge>
        </div>
      </header>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,520px)_minmax(0,1fr)]">
        {/* Board + controls */}
        <Card className="border-white/10 bg-panel">
          <CardContent className="space-y-4 py-4">
            <div className="relative mx-auto w-full max-w-[480px]">
              <div className="grid grid-cols-8 overflow-hidden rounded-lg border border-white/20 shadow-2xl" style={{ aspectRatio: "1" }}>
                {RANKS.split("").map((rank) =>
                  FILES.split("").map((file) => {
                    const sq = file + rank
                    const piece = board[sq]
                    const dark = (FILES.indexOf(file) + RANKS.indexOf(rank)) % 2 === 1
                    const isSel = selected === sq
                    const isLast = lastMove && (lastMove.from === sq || lastMove.to === sq)
                    return (
                      <button
                        key={sq}
                        onClick={() => onSquareClick(sq)}
                        className={`relative flex items-center justify-center text-4xl transition-colors select-none
                          ${dark ? "bg-[#769656]" : "bg-[#eeeed2]"}
                          ${isSel ? "ring-2 ring-inset ring-yellow-300" : ""}
                          ${isLast ? "bg-[#f6d26d]" : ""}
                          ${piece ? "cursor-pointer" : "cursor-pointer"}
                          hover:brightness-110`}
                        style={{ minHeight: 0, minWidth: 0 }}
                      >
                        {piece && (
                          <span className={`${piece === piece.toUpperCase() ? "text-[#ffffff] drop-shadow-[0_2px_2px_rgba(0,0,0,0.7)]" : "text-[#1a1a1a] drop-shadow-[0_2px_2px_rgba(255,255,255,0.3)]"}`}>
                            {PIECE_GLYPHS[piece]}
                          </span>
                        )}
                        {file === "a" && <span className="absolute top-0 left-0.5 text-[10px] font-bold text-black/50">{rank}</span>}
                        {rank === "8" && <span className="absolute right-0.5 bottom-0 text-[10px] font-bold text-black/50">{file}</span>}
                      </button>
                    )
                  })
                )}
              </div>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <label className="text-xs text-white/50">Rating</label>
                <select value={rating} onChange={(e) => setRating(Number(e.target.value))} className="rounded-md border border-white/10 bg-black/40 px-2 py-1 text-sm">
                  {[400, 500, 700, 900, 1200, 1500].map((r) => <option key={r} value={r}>{r}</option>)}
                </select>
                <label className="text-xs text-white/50">Opponent</label>
                <select value={level} onChange={(e) => setLevel(Number(e.target.value))} className="rounded-md border border-white/10 bg-black/40 px-2 py-1 text-sm">
                  <option value={1}>Gentle</option>
                  <option value={2}>Casual</option>
                  <option value={3}>Solid</option>
                  <option value={4}>Strong</option>
                </select>
              </div>
              <div className="flex gap-2">
                <Button size="sm" variant="outline" onClick={undoMove} disabled={history.length === 0}>↺ Undo</Button>
                <Button size="sm" variant="outline" onClick={newGame}>New game</Button>
              </div>
            </div>

            {history.length > 0 && (
              <div className="rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-xs text-white/60">
                {history.map((m, i) => <span key={i} className="mr-2">{m}</span>)}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Feedback + practice */}
        <div className="space-y-4">
          {result && result.ok === false && (
            <Card className="border-red-400/40 bg-red-950/10">
              <CardContent className="py-3 text-sm text-red-200">
                {result.error}
                {result.legal_moves && <div className="mt-1 text-xs text-white/40">Legal: {result.legal_moves.join(", ")}</div>}
              </CardContent>
            </Card>
          )}

          {evaluating && <div className="text-sm text-white/50">Analyzing with Stockfish 18…</div>}

          {result && result.ok === true && (
            <Card className="border-white/10 bg-panel">
              <CardHeader>
                <div className="flex items-center justify-between">
                  <CardTitle className="text-base">{result.san}</CardTitle>
                  {result.classification && (
                    <Badge className={CLASS_COLOR[result.classification] ?? ""}>{result.classification}</Badge>
                  )}
                </div>
                <CardDescription>
                  {result.win_delta_pct != null && (
                    <span className="text-white/70">
                      Win chance: {result.win_before_pct}% → {result.win_after_pct}% ({result.win_delta_pct > 0 ? "+" : ""}{result.win_delta_pct}%)
                    </span>
                  )}
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                {result.is_checkmate && <div className="text-emerald-300">Checkmate! Well played.</div>}
                {result.is_stalemate && <div className="text-amber-300">Stalemate — a draw.</div>}
                {result.explanation && (
                  <div>
                    <button className="text-xs font-semibold uppercase tracking-wide text-white/50 hover:text-white" onClick={() => setExplanationOpen((o) => !o)}>
                      {explanationOpen ? "▾ Why" : "▸ Why"}
                    </button>
                    {explanationOpen && (
                      <pre className="mt-1 whitespace-pre-wrap rounded-lg border border-emerald-400/20 bg-emerald-950/10 p-3 text-sm leading-relaxed text-emerald-100/90">{result.explanation}</pre>
                    )}
                  </div>
                )}
              </CardContent>
            </Card>
          )}

          <Card className="border-white/10 bg-panel">
            <CardHeader>
              <CardTitle className="text-base">Practice positions</CardTitle>
              <CardDescription>Curated starting points — the board resets to each one.</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="grid gap-2 sm:grid-cols-2">
                {practice.map((p) => (
                  <button
                    key={p.slug}
                    onClick={() => resetToPractice(p)}
                    className="rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-left text-sm hover:bg-black/40"
                  >
                    <div className="font-semibold text-white/90">{p.name}</div>
                    <div className="text-xs text-white/50">{p.goal}</div>
                    <div className="mt-1"><Badge className="border-white/20 bg-white/5 text-white/60">Tier {p.tier}</Badge></div>
                  </button>
                ))}
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}
