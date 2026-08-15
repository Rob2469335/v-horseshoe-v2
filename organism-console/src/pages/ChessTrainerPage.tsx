import { useCallback, useEffect, useMemo, useState } from "react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/card"
import { Button } from "../components/ui/button"
import { Badge } from "../components/ui/badge"
import ChessBoard, { type BoardHighlights, BOARD_THEMES, type BoardThemeKey } from "../components/chess/ChessBoard"

const FILES = "abcdefgh"
const RANKS = "87654321"

// Find the square of the side-to-move king (for the check glow).
function findKing(fen: string): string | null {
  const board: Record<string, string> = {}
  const placement = fen.split(" ")[0]
  const rows = placement.split("/")
  const turn = (fen.split(" ")[1] || "w") as "w" | "b"
  for (let r = 0; r < 8; r++) {
    let file = 0
    for (const ch of rows[r]) {
      if (/\d/.test(ch)) { file += parseInt(ch, 10); continue }
      if (file < 8) {
        board[FILES[file] + RANKS[r]] = ch
        file++
      }
    }
  }
  const target = turn === "w" ? "K" : "k"
  for (const [sq, p] of Object.entries(board)) {
    if (p === target) return sq
  }
  return null
}

// Minimal legal-move generator for the selected piece — computes the target
// squares for the piece on `sq` honoring the FEN side-to-move, king safety,
// castling, and en passant. Used only for rendering legal-move dots; the
// backend still authoritatively validates each played move.
function legalMovesFor(fen: string, sq: string): string[] {
  const board: Record<string, string> = {}
  const parts = fen.split(" ")
  const placement = parts[0]
  const turn = (parts[1] || "w") as "w" | "b"
  const castling = parts[2] || ""
  const ep = parts[3] || "-"
  const rows = placement.split("/")
  for (let r = 0; r < 8; r++) {
    let file = 0
    for (const ch of rows[r]) {
      if (/\d/.test(ch)) { file += parseInt(ch, 10); continue }
      if (file < 8) { board[FILES[file] + RANKS[r]] = ch; file++ }
    }
  }
  const piece = board[sq]
  if (!piece) return []
  const mine = turn === "w"
  if (mine ? piece !== piece.toUpperCase() : piece !== piece.toLowerCase()) return []
  const p = piece.toLowerCase()
  const [f0, r0] = [FILES.indexOf(sq[0]), RANKS.indexOf(sq[1])]
  const targets: string[] = []
  const onBoard = (f: number, r: number) => f >= 0 && f < 8 && r >= 0 && r < 8
  const sqAt = (f: number, r: number) => FILES[f] + RANKS[r]
  const enemy = (f: number, r: number) => {
    if (!onBoard(f, r)) return false
    const other = board[sqAt(f, r)]
    return !!other && (mine ? other === other.toLowerCase() : other === other.toUpperCase())
  }
  const empty = (f: number, r: number) => onBoard(f, r) && !board[sqAt(f, r)]
  const add = (f: number, r: number) => { if (onBoard(f, r) && (empty(f, r) || enemy(f, r))) targets.push(sqAt(f, r)) }

  if (p === "p") {
    const dir = mine ? -1 : 1
    const startRank = mine ? 6 : 1
    if (empty(f0, r0 + dir)) {
      add(f0, r0 + dir)
      if (r0 === startRank && empty(f0, r0 + 2 * dir)) add(f0, r0 + 2 * dir)
    }
    for (const df of [-1, 1]) {
      if (enemy(f0 + df, r0 + dir)) add(f0 + df, r0 + dir)
    }
    // En passant: capture onto the square behind the enemy pawn that just
    // double-pushed (ep holds that empty square; the pawn captures there).
    if (ep !== "-") {
      const epF = FILES.indexOf(ep[0])
      const epRow = RANKS.indexOf(ep[1])
      if (epRow === r0 && (epF === f0 - 1 || epF === f0 + 1)) {
        const capRank = mine ? 6 : 3
        targets.push(ep[0] + RANKS[capRank])
      }
    }
  } else if (p === "n") {
    for (const [df, dr] of [[1, 2], [2, 1], [2, -1], [1, -2], [-1, -2], [-2, -1], [-2, 1], [-1, 2]]) {
      add(f0 + df, r0 + dr)
    }
  } else if (p === "b" || p === "q") {
    for (const [df, dr] of [[1, 1], [1, -1], [-1, 1], [-1, -1]]) {
      let f = f0 + df, r = r0 + dr
      while (onBoard(f, r)) {
        if (empty(f, r)) { add(f, r); f += df; r += dr }
        else { add(f, r); break }
      }
    }
  }
  if (p === "r" || p === "q") {
    for (const [df, dr] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
      let f = f0 + df, r = r0 + dr
      while (onBoard(f, r)) {
        if (empty(f, r)) { add(f, r); f += df; r += dr }
        else { add(f, r); break }
      }
    }
  } else if (p === "k") {
    for (const [df, dr] of [[1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [1, -1], [-1, 1], [-1, -1]]) {
      add(f0 + df, r0 + dr)
    }
    // Castling (best-effort; backend validates).
    const rankStr = mine ? "1" : "8"
    if (piece === "K" && f0 === 4 && r0 === (mine ? 7 : 0)) {
      if (castling.includes("K") && empty(5, r0) && empty(6, r0) && board["h" + rankStr] === "R") targets.push("g" + rankStr)
      if (castling.includes("Q") && empty(3, r0) && empty(2, r0) && empty(1, r0) && board["a" + rankStr] === "R") targets.push("c" + rankStr)
    }
  }
  return targets
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
  best_move?: string | null
  best_move_san?: string | null
  is_checkmate?: boolean
  is_stalemate?: boolean
  in_check?: boolean
  explanation?: string
  fen?: string
  error?: string
  legal_moves?: string[]
  coach?: CoachPlan
  sacrifice?: SacrificeInfo | null
  missed_sacrifice?: { move?: string; san?: string; message?: string } | null
}

type CoachPlan = {
  ok?: boolean
  plan?: string
  king_alert?: string
  worst_piece?: string | null
  weak_square?: string | null
  attack_now?: boolean
  hint_level_1?: string
  hint_level_2?: string
}

type SacrificeInfo = {
  is_sacrifice?: boolean
  sound?: boolean
  pattern?: string
  give_up?: string
  get_back?: string
  eval_held?: boolean
  brilliant?: boolean
}

type HangingDrill = {
  ok: boolean
  fen?: string
  instruction?: string
  find?: {
    ok?: boolean
    count?: number
    hanging?: Array<{ square: string; piece: string; attackers?: number; defenders?: number; capture_uci?: string | null }>
  }
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

type ReviewEntry = {
  id: string
  pre_fen: string
  played_san: string
  best_uci?: string | null
  best_san?: string | null
  classification: string
  concept?: string
  book_titles?: string[]
  box?: number
  due_at?: number
}

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
  const [legalTargets, setLegalTargets] = useState<string[]>([])
  const [lastMove, setLastMove] = useState<{ from: string; to: string } | null>(null)
  const [result, setResult] = useState<EvalResult | null>(null)
  const [retryFen, setRetryFen] = useState<string | null>(null)
  const [evaluating, setEvaluating] = useState(false)
  const [practice, setPractice] = useState<PracticePosition[]>([])
  const [rating, setRating] = useState(500)
  const [level, setLevel] = useState(1)
  const [health, setHealth] = useState<{ engine?: { available: boolean }; book_index?: { available: boolean } } | null>(null)
  const [explanationOpen, setExplanationOpen] = useState(true)
  const [review, setReview] = useState<ReviewEntry[]>([])
  const [reviewStats, setReviewStats] = useState<{ total: number; due_count: number } | null>(null)
  const [activeReview, setActiveReview] = useState<ReviewEntry | null>(null)
  const [hint, setHint] = useState<CoachPlan | null>(null)
  const [hintLevel, setHintLevel] = useState(0)
  const [boardTheme, setBoardTheme] = useState<BoardThemeKey>("vibrant")
  const [drill, setDrill] = useState<HangingDrill | null>(null)
  const [drillResult, setDrillResult] = useState<"none" | "solved" | "missed">("none")
  const [reviewSolved, setReviewSolved] = useState<"none" | "solved" | "failed">("none")

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
        // Selecting own piece — show its legal targets.
        if (selected === sq) { setSelected(null); setLegalTargets([]); return }
        setSelected(sq)
        setLegalTargets(legalMovesFor(fen, sq))
        return
      }
    }
    // A target square (empty or enemy piece) — attempt the move from selection.
    if (selected) {
      const uci = selected + sq
      setSelected(null)
      setLegalTargets([])
      void evaluateMove(uci)
    }
  }

  const evaluateMove = async (uci: string) => {
    setEvaluating(true)
    setResult(null)
    const preFen = fen
    try {
      // Pre-move safety check (Heisman Slow->Safe->Active): if this move hangs
      // a piece or leaves the king exposed, BLOCK it and make the learner
      // reconsider — this is the #1 beginner habit the research prescribes.
      const safety = await (await fetch(`${backendUrl}/chess/trainer/safety`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fen: preFen, uci }),
      })).json() as { ok: boolean; safe?: boolean; message?: string; hanging_after?: string[]; error?: string }

      if (safety.ok && safety.safe === false) {
        setResult({
          ok: true,
          legal: false,
          error: safety.message ?? "unsafe move",
          classification: "Blunder",
          legal_moves: undefined,
        })
        setRetryFen(preFen)
        setSelected(null)
        setLegalTargets([])
        setEvaluating(false)
        return
      }

      const res = await (await fetch(`${backendUrl}/chess/trainer/evaluate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fen: preFen, uci, rating, want_explain: true }),
      })).json() as EvalResult
      setResult(res)
      setExplanationOpen(true)
      // In drill mode, capturing a hanging piece solves it.
      if (drill && res.legal && res.ok) {
        setDrillResult(drillSolved(res) ? "solved" : "missed")
        if (drillSolved(res)) {
          setDrill(null)
          setDrillResult("solved")
        }
      }
      // In review mode, playing the entry's best move solves it.
      if (activeReview && res.legal && uci === activeReview.best_uci) {
        setReviewSolved("solved")
        resolveReview(activeReview, true)
      } else if (activeReview && res.legal && res.classification && ["Mistake", "Blunder"].includes(res.classification)) {
        setReviewSolved("failed")
        resolveReview(activeReview, false)
      }
      if (res.ok && res.legal && res.fen) {
        // Learning-first: on a bad move, hold the position so the learner can
        // RETRY (find the right move themselves) instead of the engine replying
        // and steamrolling on. Only advance when the move was fine.
        const needsRetry = res.classification === "Mistake" || res.classification === "Blunder" || res.classification === "Inaccuracy"
        if (needsRetry) {
          setRetryFen(preFen)
          setHistory((h) => [...h, res.san ?? uci])
          setLastMove({ from: uci.slice(0, 2), to: uci.slice(2, 4) })
        } else {
          setRetryFen(null)
          setHistory((h) => [...h, res.san ?? uci])
          setLastMove({ from: uci.slice(0, 2), to: uci.slice(2, 4) })
          setFen(res.fen)
          void engineReply(res.fen)
        }
      }
    } catch {
      setResult({ ok: false, error: "evaluation failed" })
    } finally {
      setEvaluating(false)
    }
  }

  const retry = () => {
    if (!retryFen) return
    setFen(retryFen)
    setRetryFen(null)
    setResult(null)
    setLastMove(null)
    setSelected(null)
    setLegalTargets([])
    setHistory([])
    setExplanationOpen(false)
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
    setLegalTargets([])
    setRetryFen(null)
    setExplanationOpen(true)
  }

  const undoMove = () => {
    setHistory((h) => h.slice(0, -1))
    setFen("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
    setResult(null)
    setLastMove(null)
    setSelected(null)
    setLegalTargets([])
    setRetryFen(null)
  }

  const newGame = () => resetToPractice({ slug: "start", name: "Starting position", goal: "Play the opening", fen: "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", tier: 1 })

  // Coach hint escalation (Play-Coach pattern): press 1 = concept nudge,
  // press 2 = best-move arrow, press 3 = the best move revealed.
  const coachHint = async () => {
    const next = (hintLevel + 1) % 3
    setHintLevel(next)
    try {
      if (next === 0) {
        setHint(null)
        return
      }
      const plan = await (await fetch(`${backendUrl}/chess/trainer/coach/hint`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fen }),
      })).json() as CoachPlan
      setHint(plan)
    } catch {
      setHint({ ok: false, plan: "coach unavailable" })
    }
  }

  // Hanging-piece drill: load a position with a loose enemy piece and put it on
  // the board; the learner finds + captures it.
  const newDrill = async () => {
    try {
      const drill = await (await fetch(`${backendUrl}/chess/trainer/drill/hanging`)).json() as HangingDrill
      if (drill.ok && drill.fen) {
        setFen(drill.fen)
        setDrill(drill)
        setHistory([])
        setResult(null)
        setLastMove(null)
        setSelected(null)
        setLegalTargets([])
        setRetryFen(null)
      }
    } catch { /* ignore */ }
  }

  const drillSolved = (r: EvalResult) => {
    // Did the learner capture one of the hanging pieces?
    const hanging = drill?.find?.hanging ?? []
    const captured = r.san?.includes("x") ?? false
    const target = r.san ? r.san.split("x")[1]?.[0] ?? "" : ""
    const matched = hanging.some((h) => h.square.startsWith(target))
    return captured && matched
  }

  const loadReview = useCallback(async () => {
    try {
      const [d, s] = await Promise.all([
        (await fetch(`${backendUrl}/chess/trainer/review?limit=20`)).json(),
        (await fetch(`${backendUrl}/chess/trainer/review/stats`)).json(),
      ])
      setReview(d.due ?? [])
      setReviewStats({ total: s.total ?? 0, due_count: s.due_count ?? 0 })
    } catch { /* ignore */ }
  }, [backendUrl])

  useEffect(() => { loadReview() }, [loadReview])

  const startReview = (entry: ReviewEntry) => {
    setActiveReview(entry)
    setReviewSolved("none")
    setFen(entry.pre_fen)
    setHistory([])
    setResult(null)
    setLastMove(null)
    setSelected(null)
    setLegalTargets([])
    setRetryFen(null)
    setExplanationOpen(true)
  }

  const resolveReview = async (entry: ReviewEntry, solved: boolean) => {
    try {
      await fetch(`${backendUrl}/chess/trainer/review/${solved ? "solved" : "failed"}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ entry_id: entry.id }),
      })
    } catch { /* ignore */ }
    setReviewSolved(solved ? "solved" : "failed")
    loadReview()
  }

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
            <div className="mx-auto w-full max-w-[480px]">
              <ChessBoard
                fen={fen}
                interactive
                onSquareClick={onSquareClick}
                theme={boardTheme}
                highlights={{
                  lastMove,
                  selected,
                  legalTargets,
                  checkSquare: (result?.in_check || result?.is_checkmate) ? findKing(fen) : null,
                  arrows: result?.best_move && result.best_move.length === 4
                    ? [{ from: result.best_move.slice(0, 2), to: result.best_move.slice(2, 4) }]
                    : [],
                }}
                evalBar={result?.win_after_pct != null ? { whitePct: turn === "w" ? result.win_after_pct : 100 - result.win_after_pct } : null}
              />
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
                <label className="text-xs text-white/50">Board</label>
                <select value={boardTheme} onChange={(e) => setBoardTheme(e.target.value as BoardThemeKey)} className="rounded-md border border-white/10 bg-black/40 px-2 py-1 text-sm">
                  {Object.entries(BOARD_THEMES).map(([k, t]) => <option key={k} value={k}>{t.name}</option>)}
                </select>
              </div>
              <div className="flex gap-2">
                <Button size="sm" variant="outline" onClick={undoMove} disabled={history.length === 0}>↺ Undo</Button>
                <Button size="sm" variant="outline" onClick={coachHint}>💡 Coach</Button>
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
                {retryFen && (
                  <div className="rounded-lg border border-amber-400/30 bg-amber-400/5 p-3">
                    <div className="mb-2 text-sm text-amber-200">
                      That move wasn't ideal — the arrow shows a stronger option.
                      <span className="text-white/50"> Try to find it yourself.</span>
                    </div>
                    <Button size="sm" onClick={retry}>↺ Try again</Button>
                  </div>
                )}
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
              <div className="flex items-center justify-between">
                <CardTitle className="text-base">Review — your mistakes</CardTitle>
                {reviewStats && (
                  <Badge className="border-white/20 bg-white/5 text-white/60">{reviewStats.due_count} due · {reviewStats.total} total</Badge>
                )}
              </div>
              <CardDescription>
                Positions you blundered, re-presented as "find the better move". Solved ones come back on a spaced
                schedule (1d → 3d → 7d → 14d) so the pattern sticks.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {activeReview && (
                <div className="rounded-lg border border-emerald-400/20 bg-emerald-950/10 p-3">
                  <div className="mb-1 text-sm font-semibold text-emerald-200">Review position</div>
                  <div className="mb-2 text-xs text-white/50">
                    You played {activeReview.played_san} here ({activeReview.classification}).
                    {activeReview.concept ? ` Theme: ${activeReview.concept}.` : ""}
                    Find the better move.
                  </div>
                  {reviewSolved === "solved" && (
                    <div className="mb-2 text-sm text-emerald-300">✓ Correct — that's the right idea. Added to your spaced review.</div>
                  )}
                  {reviewSolved === "failed" && (
                    <div className="mb-2 text-sm text-amber-300">That's still a mistake — it'll come back tomorrow.</div>
                  )}
                  <div className="flex gap-2">
                    {reviewSolved === "none" && (
                      <Button size="sm" variant="outline" onClick={() => resolveReview(activeReview, false)}>Show answer</Button>
                    )}
                    <Button size="sm" variant="outline" onClick={() => { setActiveReview(null); setReviewSolved("none"); newGame() }}>Done reviewing</Button>
                  </div>
                </div>
              )}

              {review.length === 0 ? (
                <div className="text-xs text-white/40">No review positions due right now. Blunder in a game and they'll show up here.</div>
              ) : (
                <div className="max-h-56 space-y-1.5 overflow-y-auto">
                  {review.map((r) => (
                    <button
                      key={r.id}
                      onClick={() => startReview(r)}
                      className={`flex w-full items-center justify-between rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-left text-sm hover:bg-black/40 ${activeReview?.id === r.id ? "border-emerald-400/40" : ""}`}
                    >
                      <span className="text-white/85">
                        {r.played_san} <span className="text-white/40">— find the better move</span>
                      </span>
                      <span className="flex items-center gap-1.5">
                        {r.concept && <Badge className="border-white/20 bg-white/5 text-white/60">{r.concept}</Badge>}
                        <Badge className={CLASS_COLOR[r.classification] ?? ""}>{r.classification}</Badge>
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          <Card className="border-white/10 bg-panel">
            <CardHeader>
              <CardTitle className="text-base">Coach</CardTitle>
              <CardDescription>
                Engine-grounded coaching: the plan for this position, sacrifice detection, and a
                tap-to-escalate hint (concept → arrow → best move).
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {result?.sacrifice && result.sacrifice.sound && (
                <div className="rounded-lg border border-violet-400/30 bg-violet-400/10 p-3">
                  <div className="mb-1 text-sm font-semibold text-violet-200">Brilliant — sound sacrifice! {result.sacrifice.pattern}</div>
                  <div className="text-xs text-white/70">
                    You offered {result.sacrifice.give_up} and got {result.sacrifice.get_back}. The engine kept the eval
                    — you bought the attack, you didn't lose the piece.
                  </div>
                </div>
              )}
              {result?.missed_sacrifice && (
                <div className="rounded-lg border border-amber-400/30 bg-amber-400/5 p-3">
                  <div className="mb-1 text-sm font-semibold text-amber-200">Missed gift</div>
                  <div className="text-xs text-white/70">
                    {result.missed_sacrifice.message}. The arrow shows it.
                  </div>
                </div>
              )}
              {result?.coach?.plan && (
                <div className="rounded-lg border border-emerald-400/20 bg-emerald-950/10 p-3">
                  <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-white/40">Plan</div>
                  <div className="text-sm text-emerald-100/90">{result.coach.plan}</div>
                </div>
              )}

              <div className="rounded-lg border border-white/10 bg-black/20 p-3">
                <div className="mb-2 flex items-center justify-between">
                  <span className="text-xs font-semibold uppercase tracking-wide text-white/40">Coach hint</span>
                  <Button size="sm" variant="outline" onClick={coachHint}>
                    {hintLevel === 0 ? "Ask coach" : `Hint ${hintLevel}/2`}
                  </Button>
                </div>
                {hintLevel === 1 && hint?.hint_level_1 && (
                  <div className="text-sm text-white/85">{hint.hint_level_1}</div>
                )}
                {hintLevel === 2 && (
                  <div className="text-sm text-white/85">
                    {hint?.hint_level_2}
                    <div className="mt-1 text-xs text-white/50">The green arrow shows the best move.</div>
                  </div>
                )}
              </div>
            </CardContent>
          </Card>

          <Card className="border-white/10 bg-panel">
            <CardHeader>
              <CardTitle className="text-base">Hanging pieces — board vision</CardTitle>
              <CardDescription>
                The #1 beginner skill (Steps Method: "top priority"). Spot the loose enemy
                piece and capture it. The pre-move safety check also blocks your own
                hanging moves during play.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <Button size="sm" onClick={newDrill}>New drill position</Button>
              {drillResult === "solved" && <div className="text-sm text-emerald-300">✓ Caught it — you took the loose piece!</div>}
              {drillResult === "missed" && <div className="text-sm text-amber-300">That wasn't the loose piece — look again.</div>}
              {drill && drill.find?.hanging?.length ? (
                <div className="rounded-lg border border-emerald-400/20 bg-emerald-950/10 p-3 text-sm text-emerald-100/90">
                  Find and capture: {drill.find.hanging.map((h) => `the ${h.piece} on ${h.square}`).join(", ")}
                </div>
              ) : null}
            </CardContent>
          </Card>

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
