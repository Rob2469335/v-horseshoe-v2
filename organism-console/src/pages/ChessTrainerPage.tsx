import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { AnimatePresence, motion } from "framer-motion"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/card"
import { Button } from "../components/ui/button"
import { Badge } from "../components/ui/badge"
import { ActionOnboardingCard } from "../components/ui/action-onboarding-card"
import ChessBoard, { type BoardHighlights, BOARD_THEMES, type BoardThemeKey } from "../components/chess/ChessBoard"
import { Play, RefreshCw, BookOpen, BrainCircuit } from "lucide-react"
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
      const expectedEpRow = mine ? r0 - 1 : r0 + 1
      if (epRow === expectedEpRow && (epF === f0 - 1 || epF === f0 + 1)) {
        targets.push(ep)
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
    const myK = mine ? "K" : "k"
    const myR = mine ? "R" : "r"
    if (piece === myK && f0 === 4 && r0 === (mine ? 7 : 0)) {
      if (castling.includes(mine ? "K" : "k") && empty(5, r0) && empty(6, r0) && board["h" + rankStr] === myR) targets.push("g" + rankStr)
      if (castling.includes(mine ? "Q" : "q") && empty(3, r0) && empty(2, r0) && empty(1, r0) && board["a" + rankStr] === myR) targets.push("c" + rankStr)
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
  game_over?: boolean
  in_check?: boolean
  explanation?: string
  fen?: string
  error?: string
  legal_moves?: string[]
  coach?: CoachPlan
  plan_now?: CoachPlan
  plan_state?: PlanState
  sacrifice?: SacrificeInfo | null
  missed_sacrifice?: { move?: string; san?: string; message?: string } | null
}

type PlanState = {
  ok?: boolean
  persisted?: boolean
  plan?: { name?: string; recipe?: string; trigger?: string } | null
  unchanged_moves?: number
}

type CoachPlan = {
  ok?: boolean
  plan?: string
  standard_plan?: { key?: string; name?: string; recipe?: string; trigger?: string }
  mode?: string
  king_alert?: string
  worst_piece?: string | null
  weak_square?: string | null
  attack_now?: boolean
  material?: number
  hint_level_1?: string
  hint_level_2?: string
  best_move?: string | null
  best_move_san?: string | null
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

type GameReview = {
  ok: boolean
  game_id?: string
  move_count?: number
  accuracy?: number
  curve?: Array<{ n: number; san?: string; win_pct?: number; classification?: string }>
  key_moments?: Array<{ type: string; move_n?: number; san?: string; classification?: string; win_delta_pct?: number; fen?: string }>
  phases?: Record<string, number>
  queue_mistakes?: Array<Record<string, unknown>>
  error?: string
}

type GmGame = {
  id: string
  name: string
  player: string
  white?: string
  black?: string
  year?: number
  result?: string
  move_count?: number
}

type GmSession = {
  ok: boolean
  finished?: boolean
  game_id?: string
  name?: string
  ply?: number
  fen?: string
  side_to_move?: string
  move_number?: number
  result?: string
  error?: string
}

type ChessProfile = {
  ok: boolean
  username?: string
  games?: number
  record?: { wins: number; losses: number; draws: number; white_wins: number; black_wins: number }
  opening_report?: Array<{ opening: string; games: number; score_pct: number }>
  time_controls?: Array<{ tc: string; games: number; score_pct: number }>
  think_seconds?: Record<string, number | null>
  journey?: Array<{ date: number; rating?: number | null; opp?: number | null; result?: string | null }>
  journey_summary?: { first?: number; current?: number; peak?: number; trough?: number; best_gain?: number; worst_drop?: number }
  error?: string
}

type AnalysisJob = {
  ok: boolean
  job_id?: string
  resumed?: boolean
  username?: string
  status?: string
  done_games?: number
  total_games?: number
  mistakes_queued?: number
  error?: string
}

type GmGuess = {
  ok: boolean
  correct?: boolean
  gm_move_uci?: string
  gm_move_san?: string
  error?: string
}

// Concept-training item (Repair / Reinforce / Transfer).
type TrainingItem = {
  ok?: boolean
  id?: string
  concept?: string
  skill?: string
  stage?: "repair" | "reinforce" | "transfer"
  pre_fen?: string
  solution_uci?: string
  solution_san?: string
  prompt?: string
  difficulty?: number
  box?: number
  due_at?: number
}

type TrainingProgress = {
  ok: boolean
  total_items?: number
  concepts?: Record<string, {
    repair?: number; reinforce?: number; transfer?: number
    repair_mastered?: boolean; reinforce_mastered?: boolean; transfer_mastered?: boolean
    concept_mastered?: boolean; mastery?: string; success_rate?: number
  }>
}

// STUDY MODE session — the GM's move is REVEALED + explained (no guessing).
type GmStudy = {
  ok: boolean
  finished?: boolean
  game_id?: string
  name?: string
  year?: number
  ply?: number
  total_plies?: number
  fen_before?: string
  side_to_move?: string
  move_number?: number
  gm_move_san?: string
  gm_move_uci?: string
  explanation?: string
  is_key_moment?: boolean
  critical_type?: string[]
  difficulty?: number
  think_required?: boolean
  hint?: string
  degraded?: boolean
  result?: string
  error?: string
}

type Analytics = {
  ok: boolean
  training_rating?: number | null
  games_count?: number
  moves_count?: number
  skills?: Record<string, number>
  recent?: Array<{ game_id?: string; accuracy?: number; move_count?: number; started_at?: number }>
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
  lead_in_moves?: {fen: string; san: string; uci: string}[]
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

// Win% eval curve as an SVG line chart (zero new deps — no recharts). Hover a
// point to see the move + win%; blunders are red dots, everything else blue.
function EvalCurveChart({
  curve,
}: {
  curve: Array<{ n: number; san?: string; win_pct?: number; classification?: string }>
}) {
  const [hover, setHover] = useState<number | null>(null)
  const ref = useRef<HTMLDivElement | null>(null)
  if (curve.length === 0) return null

  const W = 640
  const H = 72
  const PAD = 6
  const x = (i: number) => (curve.length === 1 ? W / 2 : PAD + (i / (curve.length - 1)) * (W - PAD * 2))
  const y = (pct: number) => H - PAD - (Math.max(0, Math.min(100, pct)) / 100) * (H - PAD * 2)

  const pts = curve.map((p, i) => `${x(i).toFixed(1)},${y(p.win_pct ?? 50).toFixed(1)}`)
  const line = pts.join(" ")
  const area = `M ${pts[0].split(",")[0]} ${H - PAD} L ${line.replace(/ /g, " L ")} L ${x(curve.length - 1)} ${H - PAD} Z`

  const onMouseMove = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!ref.current) return
    const rect = ref.current.getBoundingClientRect()
    const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width))
    setHover(Math.round(ratio * (curve.length - 1)))
  }

  return (
    <div ref={ref} className="relative" onMouseMove={onMouseMove} onMouseLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${W} ${H}`} className="h-16 w-full" preserveAspectRatio="none">
        <polygon points={area} fill="#4da3e0" fillOpacity="0.12" />
        <polyline
          points={line}
          fill="none"
          stroke="#4da3e0"
          strokeWidth="2"
          vectorEffect="non-scaling-stroke"
          strokeLinejoin="round"
        />
        {curve.map((p, i) => (
          <circle
            key={p.n}
            cx={x(i)}
            cy={y(p.win_pct ?? 50)}
            r={hover === i ? 4 : 2.5}
            fill={(p.classification ?? "").startsWith("B") ? "#e05b4d" : "#4da3e0"}
            vectorEffect="non-scaling-stroke"
          />
        ))}
        {hover !== null && (
          <line
            x1={x(hover)}
            y1={PAD}
            x2={x(hover)}
            y2={H - PAD}
            stroke="rgba(255,255,255,0.25)"
            strokeWidth="1"
            vectorEffect="non-scaling-stroke"
          />
        )}
      </svg>
      {hover !== null && curve[hover] && (
        <div className="pointer-events-none absolute -top-7 left-1/2 -translate-x-1/2 whitespace-nowrap rounded border border-white/20 bg-black/90 px-2 py-0.5 text-[10px] text-white/90">
          {curve[hover].n}. {curve[hover].san ?? ""} · {curve[hover].win_pct ?? "?"}%
        </div>
      )}
    </div>
  )
}

export default function ChessTrainerPage() {
  const backendUrl = "http://127.0.0.1:8000"
  const engineAbortRef = useRef<AbortController | null>(null)
  const autoAdvanceTimeoutRef = useRef<NodeJS.Timeout | null>(null)
  const [fen, setFen] = useState("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
  const [fenHistory, setFenHistory] = useState<string[]>([]) // FEN snapshots for undo
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
  const [leadInIndex, setLeadInIndex] = useState<number | null>(null)
  const [hint, setHint] = useState<CoachPlan | null>(null)
  const [hintLevel, setHintLevel] = useState(0)
  const [boardTheme, setBoardTheme] = useState<BoardThemeKey>("vibrant")
  const [drill, setDrill] = useState<HangingDrill | null>(null)
  const [drillResult, setDrillResult] = useState<"none" | "solved" | "missed">("none")
  const [gameId, setGameId] = useState<string | null>(null)
  const [gameReview, setGameReview] = useState<GameReview | null>(null)
  const [reviewing, setReviewing] = useState(false)
  const [reviewSolved, setReviewSolved] = useState<"none" | "solved" | "failed" | "sequel">("none")
  const [isSequelDrill, setIsSequelDrill] = useState(false)
  const [radarItems, setRadarItems] = useState<any[] | null>(null)
  const [activeRadarItem, setActiveRadarItem] = useState<any | null>(null)
  const [radarSolved, setRadarSolved] = useState<"none" | "correct" | "incorrect">("none")
  const [tips, setTips] = useState<Array<{ tip: string; source: string; category: string }> | null>(null)
  const [tipsLoading, setTipsLoading] = useState(false)
  const [topMistakes, setTopMistakes] = useState<Array<{
    concept: string; count: number; share_pct: number; severity: Record<string, number>
    examples: Array<{ played_san?: string; best_san?: string; classification?: string }>
  }> | null>(null)
  const [coach, setCoach] = useState<{
    ok: boolean; total: number; skills: Record<string, { count: number; share_pct: number; bar: number }>
    top_concepts?: Array<[string, number]>; focus_skill?: string; focus?: string
  } | null>(null)
  const [gmTrainMode, setGmTrainMode] = useState(true) // Train (active recall) vs Explore (passive)
  const [gmRevealed, setGmRevealed] = useState(false)  // has the current think position been revealed?
  const [gmConfidence, setGmConfidence] = useState<"guess" | "idea" | "confident" | null>(null)
  const [trainingItem, setTrainingItem] = useState<TrainingItem | null>(null)
  const [trainingProgress, setTrainingProgress] = useState<TrainingProgress | null>(null)
  const [trainingAnswered, setTrainingAnswered] = useState<{ correct: boolean; mastered?: boolean; retired?: boolean } | null>(null)
  const [trainingConfidence, setTrainingConfidence] = useState<"guess" | "idea" | "confident" | null>(null)
  const [trainingConfidenceAt, setTrainingConfidenceAt] = useState<number | null>(null)
  const [trainingCalibration, setTrainingCalibration] = useState<Record<string, any> | null>(null)
  const [trainingBuilding, setTrainingBuilding] = useState(false)
  // Persist the eval bar between moves so it doesn't vanish when the engine replies.
  const [lastEvalPct, setLastEvalPct] = useState<number | null>(null)

  const board = useMemo(() => parseFen(fen), [fen])
  const turn = useMemo(() => sideToMove(fen), [fen])

  // Clear coach hint + Socratic dialogue when the board state changes.
  useEffect(() => {
    setHint(null)
    setHintLevel(0)
    setSocraticMsgs([])
    setSocraticInput("")
  }, [fen])

  const refreshHealth = useCallback(async () => {
    try {
      const h = await (await fetch(`${backendUrl}/chess/trainer/health`)).json()
      setHealth(h)
    } catch { setHealth(null) }
  }, [backendUrl])

  useEffect(() => { refreshHealth() }, [refreshHealth])

  // Load 10 book-grounded chess tips on mount — each page load / restart of the
  // trainer surfaces a fresh set drawn from the 100-book chess library.
  const loadTips = useCallback(async () => {
    setTipsLoading(true)
    try {
      const r = await (await fetch(`${backendUrl}/chess/trainer/tips?count=10`)).json()
      setTips(r.tips ?? [])
    } catch {
      setTips(null)
    } finally {
      setTipsLoading(false)
    }
  }, [backendUrl])

  useEffect(() => { loadTips() }, [loadTips])

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
      if (!legalTargets.includes(sq)) {
        setSelected(null)
        setLegalTargets([])
        return
      }
      let uci = selected + sq
      // Auto-queen for pawn promotion
      const p = board[selected]
      if (p && p.toLowerCase() === "p") {
        const rank = sq[1]
        if (rank === "1" || rank === "8") {
          const promo = window.prompt("Promote to (q/r/b/n):", "q")
          if (promo === null) {
            setSelected(null)
            setLegalTargets([])
            return
          }
          if (["q", "r", "b", "n"].includes(promo.toLowerCase())) {
            uci += promo.toLowerCase()
          } else {
            uci += "q"
          }
        }
      }
      setSelected(null)
      setLegalTargets([])
      void evaluateMove(uci)
    }
  }

  const evaluateMove = async (uci: string) => {
    setEvaluating(true)
    setResult(null)
    const preFen = fen
    const controller = new AbortController()
    engineAbortRef.current = controller

    try {
      // Pre-move safety check (Heisman Slow->Safe->Active): if this move hangs
      // a piece or leaves the king exposed, BLOCK it and make the learner
      // reconsider — this is the #1 beginner habit the research prescribes.
      const safety = await (await fetch(`${backendUrl}/chess/trainer/safety`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fen: preFen, uci }),
        signal: controller.signal,
      })).json() as { ok: boolean; safe?: boolean; message?: string; hanging_after?: string[]; error?: string }

      if (safety.ok && safety.safe === false) {
        setResult({
          ok: true,
          legal: false,
          error: safety.message ?? "unsafe move",
          classification: "Blunder",
          legal_moves: undefined,
        })
        if (!retryFen) {
          setRetryFen(preFen)
          setHistory((h) => [...h, uci])
        }
        setSelected(null)
        setLegalTargets([])
        setEvaluating(false)
        return
      }

      const res = await (await fetch(`${backendUrl}/chess/trainer/evaluate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fen: preFen, uci, rating, want_explain: true, game_id: gameId }),
        signal: controller.signal,
      })).json() as EvalResult
      setResult(res)
      setExplanationOpen(true)
      // Persist the eval bar — update on every evaluation. win_after_pct is the
      // MOVER's win%, so normalize to White's share NOW (the mover is the player;
      // turn at render time is the opponent's once the board advances, which would
      // invert the bar for a Black-side player or after the engine replies).
      if (res.ok && res.win_after_pct != null) {
        setLastEvalPct(sideToMove(preFen) === "w" ? res.win_after_pct : 100 - res.win_after_pct)
      }
      // In drill mode, capturing a hanging piece solves it.
      if (drill && res.legal && res.ok) {
        setDrillResult(drillSolved(res) ? "solved" : "missed")
        if (drillSolved(res)) {
          setDrill(null)
          setDrillResult("solved")
        }
      }
      // In review mode, playing the entry's best move solves it, OR finding an equally good move.
      if (activeReview && res.legal) {
        // Accept the stored best move, OR any move Stockfish now classifies as Excellent or better
        const isPassed = uci === activeReview.best_uci || (res.classification && ["Best", "Excellent", "Good", "Brilliant"].includes(res.classification));
        if (isPassed) {
          if (!isSequelDrill && activeReview) {
            setIsSequelDrill(true)
            setReviewSolved("sequel")
            try {
              const reply = await fetch(`${backendUrl}/chess/trainer/engine-strong`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ fen: res.fen })
              }).then(r => r.json())
              if (reply.ok && reply.uci && !reply.is_checkmate) {
                setFen(reply.fen)
                setHistory((h) => [...h, res.san ?? uci, reply.san ?? reply.uci])
                setFenHistory((h) => [...h, res.fen, reply.fen])
                setLastMove({ from: reply.uci.slice(0, 2), to: reply.uci.slice(2, 4) })
                setResult(null)
                setHint(null)
                setHintLevel(0)
                return
              } else {
                setReviewSolved("solved")
                resolveReview(activeReview, true)
                setIsSequelDrill(false)
              }
            } catch {
              setReviewSolved("solved")
              resolveReview(activeReview, true)
              setIsSequelDrill(false)
            }
          } else {
            setReviewSolved("solved")
            if (activeReview) resolveReview(activeReview, true)
            setIsSequelDrill(false)
          }
        } else if (res.classification && ["Mistake", "Blunder", "Inaccuracy"].includes(res.classification)) {
          setReviewSolved("failed")
          if (activeReview) resolveReview(activeReview, false)
          setIsSequelDrill(false)
        }
      }
      if (res.ok && res.legal && res.fen) {
        // Learning-first: on a bad move, hold the position so the learner can
        // RETRY (find the right move themselves) instead of the engine replying
        // and steamrolling on. Only advance when the move was fine.
        const needsRetry = res.classification === "Mistake" || res.classification === "Blunder" || res.classification === "Inaccuracy"
        if (needsRetry) {
          if (!retryFen) {
            setRetryFen(preFen)
            setHistory((h) => [...h, res.san ?? uci])
          }
          setLastMove({ from: uci.slice(0, 2), to: uci.slice(2, 4) })
        } else {
          setRetryFen(null)
          setHistory((h) => {
            const baseHistory = retryFen ? h.slice(0, -1) : h;
            return [...baseHistory, res.san ?? uci];
          })
          setLastMove({ from: uci.slice(0, 2), to: uci.slice(2, 4) })
          setFenHistory((fh) => [...fh, preFen])
          setFen(res.fen)
          if (!res.is_checkmate && !res.is_stalemate && !res.game_over) {
            await engineReply(res.fen)
          }
        }
      }
    } catch (e: any) {
      if (e?.name === "AbortError") return
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
    setHistory((h) => h.slice(0, -1))
    setExplanationOpen(false)
  }

  const engineReply = async (playerFen: string) => {
    const controller = new AbortController()
    engineAbortRef.current = controller
    let isTimeout = false
    const timeout = setTimeout(() => {
      isTimeout = true
      controller.abort()
    }, 15000)
    try {
      const res = await (await fetch(`${backendUrl}/chess/trainer/engine-move`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fen: playerFen, rating, level }),
        signal: controller.signal,
      })).json() as EngineReply
      
      if (res.ok) {
        if (res.game_over || res.is_checkmate || res.is_stalemate) {
          setResult(res as any)
        }
        if (res.fen) {
          setHistory((h) => [...h, res.san ?? res.uci ?? ""])
          if (res.uci) setLastMove({ from: res.uci.slice(0, 2), to: res.uci.slice(2, 4) })
          setFenHistory((fh) => [...fh, playerFen])
          setFen(res.fen)
        }
      }
    } catch (e: any) { 
      if (isTimeout) {
        setResult({ ok: false, error: "engine timeout or network failure" } as any)
        return
      }
      if (e?.name === "AbortError") return
      setResult({ ok: false, error: "engine timeout or network failure" } as any)
    } finally {
      clearTimeout(timeout)
    }
  }

  const resetToPractice = (p: PracticePosition) => {
    engineAbortRef.current?.abort()
    setFen(p.fen)
    setFenHistory([])
    setHistory([])
    setResult(null)
    setLastMove(null)
    setSelected(null)
    setLegalTargets([])
    setRetryFen(null)
    setExplanationOpen(true)
  }

  const undoMove = () => {
    engineAbortRef.current?.abort()
    
    let popCount = 1;
    if (retryFen) {
      popCount = 2;
    } else {
      popCount = history.length % 2 === 1 ? 1 : 2;
    }
    popCount = Math.min(popCount, Math.max(1, fenHistory.length));

    setFenHistory((fh) => {
      const prev = fh[fh.length - popCount]
      if (prev) setFen(prev)
      return fh.slice(0, -popCount)
    })
    setHistory((h) => h.slice(0, -(popCount + (retryFen ? 1 : 0))))
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
    const next = (hintLevel + 1) % 4
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

  // Socratic dialogue: the coach asks a question, the learner answers, repeat.
  // The coach never names the move until the learner has been stuck several
  // turns (fail-open reveal). Board position changes reset the dialogue.
  const [socraticMsgs, setSocraticMsgs] = useState<Array<{ role: "user" | "coach"; content: string }>>([])
  const [socraticInput, setSocraticInput] = useState("")
  const [socraticBusy, setSocraticBusy] = useState(false)

  const socraticAsk = async (text: string, proposedUci?: string) => {
    const trimmed = text.trim()
    if ((!trimmed && !proposedUci) || socraticBusy) return
    setSocraticInput("")
    const content = proposedUci ? `What about playing ${proposedUci}?` : trimmed
    const updated = [...socraticMsgs, { role: "user" as const, content }]
    setSocraticMsgs(updated)
    setSocraticBusy(true)
    try {
      const r = await (await fetch(`${backendUrl}/chess/trainer/coach/socratic`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fen, history: updated, proposed_uci: proposedUci ?? null }),
      })).json() as { ok: boolean; reply?: string; error?: string }
      setSocraticMsgs((m) => [...m, { role: "coach", content: r.reply ?? r.error ?? "Coach unavailable." }])
    } catch {
      setSocraticMsgs((m) => [...m, { role: "coach", content: "Coach unavailable — the engine nudge below still helps." }])
    } finally {
      setSocraticBusy(false)
    }
  }

  // Right-click-drag a move on the board to ask the coach "what if I play this?"
  const proposeMoveToCoach = (uci: string) => {
    void socraticAsk("", uci)
  }

  // Hanging-piece drill: load a position with a loose enemy piece and put it on
  // the board; the learner finds + captures it.
  const newDrill = async () => {
    try {
      const drill = await (await fetch(`${backendUrl}/chess/trainer/drill/hanging`)).json() as HangingDrill
      if (drill.ok && drill.fen) {
        setFen(drill.fen)
        setDrill(drill)
        setFenHistory([])
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

  // Full-game guided review: start a recorded game, then finish + review it.
  const startGame = async () => {
    try {
      const g = await (await fetch(`${backendUrl}/chess/trainer/game/start`, { method: "POST" })).json() as { id: string }
      setGameId(g.id)
      setGameReview(null)
      resetToPractice({ slug: "start", name: "Starting position", goal: "Play the opening", fen: "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", tier: 1 })
    } catch { /* ignore */ }
  }

  const finishReview = async () => {
    setReviewing(true)
    try {
      const r = await (await fetch(`${backendUrl}/chess/trainer/game/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ game_id: gameId }),
      })).json() as GameReview
      setGameReview(r)
      setGameId(null)
      loadAnalytics()
    } catch {
      setGameReview({ ok: false, error: "review failed" })
    } finally {
      setReviewing(false)
    }
  }

  const queueGameMistakes = async () => {
    if (!gameReview?.game_id) return
    try {
      await fetch(`${backendUrl}/chess/trainer/game/queue-mistakes`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ game_id: gameReview.game_id }),
      })
      loadReview()
    } catch { /* ignore */ }
  }

  // Play-like-the-greats: STUDY MODE on famous Fischer/Carlsen games — each
  // GM move is revealed and explained (why, what it threatens, the plan).
  const [gmGames, setGmGames] = useState<GmGame[]>([])
  const [gmStudy, setGmStudy] = useState<GmStudy & { loading?: boolean } | null>(null)
  const gmStudyRef = useRef<GmStudy | null>(null)

  // chess.com personalization.
  const [ccUsername, setCcUsername] = useState(() => localStorage.getItem("chesscom_username") ?? "")
  const [ccProfile, setCcProfile] = useState<ChessProfile | null>(null)
  const [ccProfileLoading, setCcProfileLoading] = useState(false)
  const [ccJob, setCcJob] = useState<AnalysisJob | null>(null)
  const [ccPoll, setCcPoll] = useState<ReturnType<typeof setInterval> | null>(null)

  // Load the last chess.com username from the backend (survives any browser/
  // origin) on mount, preferring a saved value.
  useEffect(() => {
    (async () => {
      try {
        const r = await (await fetch(`${backendUrl}/chess/trainer/import/chesscom/username`)).json() as { ok: boolean; username?: string }
        if (r.ok && r.username && !ccUsername) {
          setCcUsername(r.username)
          localStorage.setItem("chesscom_username", r.username)
        }
      } catch { /* ignore */ }
    })()
  }, [backendUrl]) // eslint-disable-line react-hooks/exhaustive-deps

  const saveCcUsername = (name: string) => {
    setCcUsername(name)
    if (name.trim()) {
      localStorage.setItem("chesscom_username", name.trim())
      // Also persist on the backend so it survives host/origin changes.
      void fetch(`${backendUrl}/chess/trainer/import/chesscom/username`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: name.trim() }),
      })
    }
  }

  const buildProfile = async () => {
    if (!ccUsername.trim()) return
    setCcProfileLoading(true)
    setCcProfile(null)
    try {
      const p = await (await fetch(`${backendUrl}/chess/trainer/import/chesscom/profile`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: ccUsername.trim() }),
      })).json() as ChessProfile
      setCcProfile(p)
    } catch {
      setCcProfile({ ok: false, error: "profile build failed" })
    } finally {
      setCcProfileLoading(false)
    }
  }

  const startAnalysis = async () => {
    if (!ccUsername.trim()) return
    setCcJob(null)
    try {
      const j = await (await fetch(`${backendUrl}/chess/trainer/analysis/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: ccUsername.trim() }),
      })).json() as AnalysisJob
      setCcJob(j)
      // Poll status every 5s while running.
      if (ccPoll) clearInterval(ccPoll)
      const poll = setInterval(async () => {
        try {
          const s = await (await fetch(`${backendUrl}/chess/trainer/analysis/status/${j.job_id}`)).json() as AnalysisJob
          setCcJob(s)
          if (s.status === "done" || s.status === "error") clearInterval(poll)
        } catch { /* ignore */ }
      }, 5000)
      setCcPoll(poll)
    } catch {
      setCcJob({ ok: false, error: "could not start analysis" })
    }
  }

  // Clear the polling interval on unmount to prevent a memory leak.
  useEffect(() => {
    return () => { if (ccPoll) clearInterval(ccPoll) }
  }, [ccPoll])

  const ccLoading = ccProfileLoading || (ccJob?.status === "running")

  const loadGmGames = useCallback(async () => {
    try {
      const g = await (await fetch(`${backendUrl}/chess/trainer/gm-games`)).json() as { ok: boolean; games: GmGame[] }
      setGmGames(g.games ?? [])
    } catch { /* ignore */ }
  }, [backendUrl])

  useEffect(() => { loadGmGames() }, [loadGmGames])

  const startGm = async (gid: string) => {
    setGmStudy(null)
    setGmRevealed(false)
    setGmConfidence(null)
    try {
      // STUDY MODE: open the game at move 0 — the first GM move is shown + explained.
      const s = await (await fetch(`${backendUrl}/chess/trainer/gm-games/study`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ game_id: gid, ply: 0 }),
      })).json() as GmStudy
      if (s.ok && s.fen_before) {
        setGmStudy(s)
        setFen(s.fen_before)
        setFenHistory([])
        setHistory([])
        setResult(null)
        setLastMove(null)
      }
    } catch { /* ignore */ }
  }

  // STUDY MODE: advance one ply — show the next GM move + its explanation.
  // In TRAIN mode a think position is held (not revealed) until the learner
  // commits a confidence + Reveal; in EXPLORE mode it flows straight through.
  const gmNext = async () => {
    if (!gmStudy?.game_id || gmStudy.ply == null || gmStudy.finished) return
    try {
      setGmStudy((prev) => (prev ? { ...prev, loading: true } : prev))
      setGmRevealed(false)
      setGmConfidence(null)
      const nextPly = gmStudy.ply + 1
      const s = await (await fetch(`${backendUrl}/chess/trainer/gm-games/study`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ game_id: gmStudy.game_id, ply: nextPly }),
      })).json() as GmStudy
      if (s.ok && s.fen_before) {
        setGmStudy(s)
        setFen(s.fen_before)
        // In EXPLORE mode (or non-think moves) reveal immediately; in TRAIN
        // mode a think position waits for the learner.
        const shouldHold = gmTrainMode && s.think_required
        if (!shouldHold) {
          if (s.gm_move_uci) setLastMove({ from: s.gm_move_uci.slice(0, 2), to: s.gm_move_uci.slice(2, 4) })
          setGmRevealed(true)
        } else {
          setLastMove(null)
          setGmRevealed(false)
        }
      }
    } catch { /* ignore */ }
  }

  // Reveal the held think-position move.
  const gmReveal = () => {
    if (!gmStudy?.gm_move_uci) return
    setLastMove({ from: gmStudy.gm_move_uci.slice(0, 2), to: gmStudy.gm_move_uci.slice(2, 4) })
    setGmRevealed(true)
  }
  gmStudyRef.current = gmStudy

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

  const loadTopMistakes = useCallback(async () => {
    try {
      const r = await (await fetch(`${backendUrl}/chess/trainer/review/top?limit=10`)).json()
      setTopMistakes(r.top ?? [])
    } catch { setTopMistakes(null) }
  }, [backendUrl])

  useEffect(() => { loadTopMistakes() }, [loadTopMistakes])

  const loadCoach = useCallback(async () => {
    try {
      const c = await (await fetch(`${backendUrl}/chess/trainer/review/coach`)).json()
      setCoach(c)
    } catch { setCoach(null) }
  }, [backendUrl])

  useEffect(() => { loadCoach() }, [loadCoach])

  // Concept training: load the next due item + progress + calibration.
  const loadTraining = useCallback(async () => {
    try {
      const [d, p, c] = await Promise.all([
        (await fetch(`${backendUrl}/chess/trainer/review/training?limit=1`)).json(),
        (await fetch(`${backendUrl}/chess/trainer/review/training/progress`)).json(),
        (await fetch(`${backendUrl}/chess/trainer/review/training/calibration`)).json(),
      ])
      setTrainingItem((d.due ?? [])[0] ?? null)
      setTrainingProgress(p)
      setTrainingCalibration(c)
      setTrainingAnswered(null)
      setTrainingConfidence(null) // confidence is chosen fresh before each reveal
      setTrainingConfidenceAt(null)
    } catch { /* ignore */ }
  }, [backendUrl])

  const buildTraining = async () => {
    setTrainingBuilding(true)
    try {
      await (await fetch(`${backendUrl}/chess/trainer/review/training/build`, { method: "POST" })).json()
      await loadTraining()
      await loadCoach()
    } catch { /* ignore */ } finally { setTrainingBuilding(false) }
  }

  const answerTraining = async (correct: boolean) => {
    if (!trainingItem?.id) return
    // Invariant: confidence is chosen BEFORE the answer is submitted; it can't
    // be added after the fact (a post-hoc 'confident' is worthless calibration).
    if (!trainingConfidence) return
    try {
      const r = await (await fetch(`${backendUrl}/chess/trainer/review/training/answer`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          item_id: trainingItem.id,
          correct,
          confidence: trainingConfidence,
          // When confidence was selected (before this submit) — the backend
          // rejects a capture that lands AFTER the answer as post-hoc.
          confidence_captured_at: trainingConfidenceAt ?? undefined,
        }),
      })).json()
      setTrainingAnswered({ correct, mastered: r.mastered, retired: r.retired })
      // Load the next item + refreshed progress.
      setTimeout(() => void loadTraining(), 1200)
    } catch { /* ignore */ }
  }

  useEffect(() => { loadTraining() }, [loadTraining]) // load existing training on mount

  const [analytics, setAnalytics] = useState<Analytics | null>(null)
  const loadAnalytics = useCallback(async () => {
    try {
      const a = await (await fetch(`${backendUrl}/chess/trainer/analytics`)).json() as Analytics
      setAnalytics(a)
    } catch { /* ignore */ }
  }, [backendUrl])
  useEffect(() => { loadAnalytics() }, [loadAnalytics])

  useEffect(() => {
    return () => {
      if (autoAdvanceTimeoutRef.current) clearTimeout(autoAdvanceTimeoutRef.current)
    }
  }, [])

  const loadRadar = async () => {
    try {
      const res = await fetch(`${backendUrl}/chess/trainer/blunder-radar?limit=10`).then(r => r.json())
      if (res.ok) setRadarItems(res.items)
    } catch {}
  }

  const startRadarItem = (item: any) => {
    setActiveRadarItem(item)
    setActiveReview(null)
    setRadarSolved("none")
    setFen(item.fen)
    setFenHistory([])
    setHistory([])
    setLastMove(null)
    setResult(null)
    setHint(null)
    setHintLevel(0)
    setIsSequelDrill(false)
  }

  const guessRadar = (hasTactic: boolean) => {
    if (!activeRadarItem) return
    const isCorrect = hasTactic === activeRadarItem.has_tactic
    setRadarSolved(isCorrect ? "correct" : "incorrect")
  }

  const startReview = (entry: ReviewEntry) => {
    if (autoAdvanceTimeoutRef.current) clearTimeout(autoAdvanceTimeoutRef.current)
    if (engineAbortRef.current) engineAbortRef.current.abort()
    
    setIsSequelDrill(false)
    setActiveReview(entry)
    setReviewSolved("none")
    
    if (entry.lead_in_moves && entry.lead_in_moves.length > 0) {
      setLeadInIndex(0)
      setFen(entry.lead_in_moves[0].fen)
      setFenHistory([])
      setHistory([])
    } else {
      setLeadInIndex(null)
      setFen(entry.pre_fen)
      setFenHistory([])
      setHistory([])
    }
    
    setResult(null)
    setLastMove(null)
    setSelected(null)
    setLegalTargets([])
    setRetryFen(null)
    setExplanationOpen(true)
  }

  const playNextLeadInMove = () => {
    if (!activeReview || leadInIndex === null || !activeReview.lead_in_moves) return
    const moves = activeReview.lead_in_moves
    
    const move = moves[leadInIndex]
    const nextFen = leadInIndex + 1 < moves.length ? moves[leadInIndex + 1].fen : activeReview.pre_fen
    
    setFenHistory(prev => [...prev, fen])
    setHistory(prev => [...prev, move.san || move.uci])
    setFen(nextFen)
    
    setLastMove({
      from: move.uci.substring(0, 2),
      to: move.uci.substring(2, 4)
    })
    
    if (leadInIndex + 1 < moves.length) {
      setLeadInIndex(leadInIndex + 1)
    } else {
      setLeadInIndex(null)
    }
  }

  const advanceToNextMistake = async (currentEntryId: string) => {
    try {
      const [d, s] = await Promise.all([
        (await fetch(`${backendUrl}/chess/trainer/review?limit=20`)).json(),
        (await fetch(`${backendUrl}/chess/trainer/review/stats`)).json(),
      ])
      const newQueue = d.due ?? []
      setReview(newQueue)
      setReviewStats({ total: s.total ?? 0, due_count: s.due_count ?? 0 })
      const nextEntry = newQueue.find((r: ReviewEntry) => r.id !== currentEntryId)
      if (nextEntry) {
        startReview(nextEntry)
      } else {
        setActiveReview(null)
        setReviewSolved("none")
        newGame()
      }
    } catch { /* ignore */ }
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
    setIsSequelDrill(false)
    
    if (autoAdvanceTimeoutRef.current) clearTimeout(autoAdvanceTimeoutRef.current)
    autoAdvanceTimeoutRef.current = setTimeout(() => advanceToNextMistake(entry.id), 1500)
  }

  // Interactive board disables clicks if they haven't guessed the radar yet
  const boardInteractive = leadInIndex !== null ? false : (activeRadarItem ? radarSolved !== "none" : reviewSolved !== "solved")

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
      
      <div className="mb-4">
        <ActionOnboardingCard
          id="chess-hero"
          hero
          title="Play the Opening"
          description="The best way to learn is by playing. Make your first move on the board against the engine, and it will evaluate your play."
          actionLabel="Start a Game"
          icon={<Play size={24} />}
          onAction={() => {
            const el = document.getElementById("chess-new-game-btn")
            if (el) el.click()
            window.scrollTo({ top: 0, behavior: "smooth" })
          }}
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,520px)_minmax(0,1fr)] lg:items-start">
        {/* Board + controls — sticky so the board stays visible while you scroll
            the right-side lists (review queue, chess.com games, GM games, etc.). */}
        <Card className="border-white/10 bg-panel lg:sticky lg:top-4 lg:self-start">
          <CardContent className="space-y-4 py-4">
            <div className="mx-auto w-full max-w-[480px]">
              <ChessBoard
                fen={fen}
                interactive={boardInteractive}
                onSquareClick={onSquareClick}
                onProposeMove={proposeMoveToCoach}
                theme={boardTheme}
                highlights={{
                  lastMove,
                  selected,
                  legalTargets,
                  checkSquare: (result?.in_check || result?.is_checkmate) ? findKing(fen) : null,
                  arrows: (reviewSolved === "failed" && activeReview?.best_uci && activeReview.best_uci.length >= 4)
                    ? [{ from: activeReview.best_uci.slice(0, 2), to: activeReview.best_uci.slice(2, 4) }]
                    : (result?.best_move && result.best_move.length >= 4)
                    ? [{ from: result.best_move.slice(0, 2), to: result.best_move.slice(2, 4) }]
                    : (hintLevel >= 2 && hint?.best_move && hint.best_move.length >= 4)
                      ? [{ from: hint.best_move.slice(0, 2), to: hint.best_move.slice(2, 4) }]
                      : [],
                }}
                evalBar={lastEvalPct != null ? { whitePct: lastEvalPct } : null}
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
                <Button id="chess-coach-btn" size="sm" variant="outline" onClick={coachHint}>💡 Coach</Button>
                <Button id="chess-new-game-btn" size="sm" variant="outline" onClick={newGame}>New game</Button>
              </div>
            </div>

            {history.length > 0 && (
              <div className="rounded-lg border border-white/10 bg-black/20 px-3 py-2 font-mono text-xs text-white/60">
                {history.reduce((pairs: string[][], m, i) => {
                  if (i % 2 === 0) pairs.push([m])
                  else pairs[pairs.length - 1].push(m)
                  return pairs
                }, []).map((pair, i) => (
                  <span key={i} className="mr-3 inline-block">
                    <span className="text-white/30">{i + 1}.</span>{" "}
                    <span className="text-white/80">{pair[0]}</span>
                    {pair[1] && <span className="text-white/60"> {pair[1]}</span>}
                  </span>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Feedback + practice */}
        <div className="space-y-4">

          {/* Prominent retry banner — sits at the very top so it can't be missed */}
          <AnimatePresence>
            {retryFen && (
              <motion.div
                key="retry-banner"
                initial={{ opacity: 0, y: -12 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -12 }}
                transition={{ type: "spring", stiffness: 320, damping: 28 }}
                className="rounded-xl border-2 border-amber-400/60 bg-amber-950/30 p-4 shadow-lg shadow-amber-900/20"
              >
                <div className="mb-1 text-base font-bold text-amber-300">
                  {result?.classification === "Blunder" ? "💥 Blunder!" : result?.classification === "Mistake" ? "❌ Mistake" : "⚠️ Inaccuracy"}
                </div>
                <div className="mb-3 text-sm text-amber-100/80">
                  That wasn't the best move — the green arrow shows a stronger option. Find it yourself!
                </div>
                <Button size="sm" onClick={retry} className="bg-amber-500/20 hover:bg-amber-500/30 border border-amber-400/40 text-amber-200">
                  🔄 Try a different move
                </Button>
              </motion.div>
            )}
          </AnimatePresence>

          {result && result.ok === false && (
            <Card className="border-red-400/40 bg-red-950/10">
              <CardContent className="py-3 text-sm text-red-200">
                {result.error}
                {result.legal_moves && <div className="mt-1 text-xs text-white/40">Legal: {result.legal_moves.join(", ")}</div>}
              </CardContent>
            </Card>
          )}

          {evaluating && (
            <div className="flex items-center gap-2 text-sm text-white/50">
              <span>Stockfish analyzing</span>
              <span className="flex gap-0.5">
                {[0, 1, 2].map((i) => (
                  <motion.span
                    key={i}
                    className="inline-block h-1.5 w-1.5 rounded-full bg-emerald-400/70"
                    animate={{ opacity: [0.2, 1, 0.2] }}
                    transition={{ duration: 1.2, repeat: Infinity, delay: i * 0.2 }}
                  />
                ))}
              </span>
            </div>
          )}

          <AnimatePresence mode="wait">
            {result && result.ok === true && (
              <motion.div
                key={result.san ?? "result"}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={{ type: "spring", stiffness: 280, damping: 26 }}
              >
                <Card className={`border ${
                  result.classification === "Best" || result.classification === "Brilliant" ? "border-emerald-400/40 bg-emerald-950/10"
                  : result.classification === "Good" || result.classification === "Excellent" ? "border-sky-400/30 bg-sky-950/10"
                  : result.classification === "Inaccuracy" ? "border-amber-400/30 bg-amber-950/10"
                  : result.classification === "Mistake" || result.classification === "Blunder" ? "border-red-400/30 bg-red-950/10"
                  : "border-white/10 bg-panel"
                }`}>
                  <CardHeader>
                    <div className="flex items-center justify-between">
                      <CardTitle className="text-base font-mono">{result.san}</CardTitle>
                      <div className="flex items-center gap-2">
                        {result.classification && (
                          <Badge className={CLASS_COLOR[result.classification] ?? ""}>{result.classification}</Badge>
                        )}
                      </div>
                    </div>
                    {/* Plain-English label — the number follows in small text */}
                    <div className="text-sm font-medium mt-1">
                      {result.is_checkmate ? <span className="text-emerald-300">♟ Checkmate — you won!</span>
                      : result.is_stalemate ? <span className="text-amber-300">½ Stalemate — it's a draw.</span>
                      : result.classification === "Brilliant" ? <span className="text-violet-300">💎 Brilliant! Engine-level move.</span>
                      : result.classification === "Best" ? <span className="text-emerald-300">✅ Best move — that's what Stockfish plays.</span>
                      : result.classification === "Excellent" ? <span className="text-emerald-200">👍 Excellent — near-best play.</span>
                      : result.classification === "Good" ? <span className="text-sky-300">👍 Good move — solid choice.</span>
                      : result.classification === "Inaccuracy" ? <span className="text-amber-300">⚠️ Inaccuracy — a slightly better option existed.</span>
                      : result.classification === "Mistake" ? <span className="text-orange-300">❌ Mistake — this loses some advantage.</span>
                      : result.classification === "Blunder" ? <span className="text-red-300">💥 Blunder — this loses a lot of material or advantage.</span>
                      : null}
                    </div>
                    {result.win_delta_pct != null && (
                      <CardDescription className="mt-1">
                        <span className="text-white/50 text-xs">
                          Win%: {result.win_before_pct}% → {result.win_after_pct}%
                          {" "}({result.win_delta_pct > 0 ? "+" : ""}{result.win_delta_pct}%)
                        </span>
                      </CardDescription>
                    )}
                  </CardHeader>
                  <CardContent className="space-y-3">
                    {result.explanation && (
                      <div>
                        <button className="text-xs font-semibold uppercase tracking-wide text-white/50 hover:text-white" onClick={() => setExplanationOpen((o) => !o)}>
                          {explanationOpen ? "▾ Why" : "▸ Why"}
                        </button>
                        {explanationOpen && (
                          <pre className="mt-1 whitespace-pre-wrap rounded-lg border border-white/10 bg-black/20 p-3 text-sm leading-relaxed text-white/85">{result.explanation}</pre>
                        )}
                      </div>
                    )}
                  </CardContent>
                </Card>
              </motion.div>
            )}
          </AnimatePresence>

          {/* ── 1. COACH (live play feedback — most immediately useful) ── */}
          <Card className="border-white/10 bg-panel">
            <CardHeader>
              <CardTitle className="text-base">Coach</CardTitle>
              <CardDescription>
                Engine-grounded coaching: the plan for this position, sacrifice detection, and a
                tap-to-escalate hint (concept → arrow → best move).
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {!result && (
                <ActionOnboardingCard
                  id="chess-coach"
                  title="Play-Coach Pattern"
                  description="When you get stuck, tap the Coach button for a progressive hint. First it gives a concept, then draws an arrow, and finally reveals the best move."
                  actionLabel="Ask Coach"
                  icon={<BrainCircuit size={20} />}
                  onAction={() => {
                    const el = document.getElementById("chess-coach-btn")
                    if (el) el.click()
                  }}
                />
              )}

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
              {result?.plan_state?.plan && (
                <div className="rounded-lg border border-amber-400/30 bg-amber-950/10 p-3">
                  <div className="mb-1 flex items-center justify-between text-xs font-semibold uppercase tracking-wide text-white/40">
                    <span>Current plan · {result.plan_state.plan.name}</span>
                    <span>{(result.plan_state.unchanged_moves ?? 0) > 0 ? `holding ${result.plan_state.unchanged_moves}+ moves` : "new"}</span>
                  </div>
                  <div className="text-sm font-medium text-amber-100">{result.plan_state.plan.recipe}</div>
                  {result.plan_state.plan.trigger && (
                    <div className="mt-0.5 text-xs text-white/50">trigger: {result.plan_state.plan.trigger}</div>
                  )}
                </div>
              )}
              {result?.coach?.plan && (
                <div className="rounded-lg border border-emerald-400/20 bg-emerald-950/10 p-3">
                  <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-white/40">Plan</div>
                  <div className="text-sm text-emerald-100/90">{result.coach.plan}</div>
                  {result.coach.material != null && result.coach.material !== 0 && (
                    <div className="mt-0.5 text-xs text-white/50">
                      material: {result.coach.material > 0 ? "+" : ""}{result.coach.material}
                    </div>
                  )}
                </div>
              )}

              <div className="rounded-lg border border-white/10 bg-black/20 p-3">
                <div className="mb-2 flex items-center justify-between">
                  <span className="text-xs font-semibold uppercase tracking-wide text-white/40">Coach hint</span>
                  <div className="flex items-center gap-2">
                    <div className="flex items-center gap-1" aria-label="Hint escalation">
                      {[1, 2, 3].map((n) => (
                        <span
                          key={n}
                          className={`inline-block h-2 w-2 rounded-full transition-colors ${
                            hintLevel >= n ? "bg-amber-400" : "bg-white/15"
                          }`}
                        />
                      ))}
                    </div>
                    <Button size="sm" variant="outline" onClick={coachHint}>
                      {hintLevel === 0 ? "Ask coach" : `Hint ${hintLevel}/3`}
                    </Button>
                  </div>
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
                {hintLevel === 3 && hint?.best_move_san && (
                  <div className="text-sm text-white/85">
                    The best move is <span className="font-semibold text-emerald-400">{hint.best_move_san}</span>.
                  </div>
                )}
              </div>

              {/* Socratic dialogue — the coach asks, you answer, it reacts.
                  Never names the move until you've been stuck several turns. */}
              <div className="rounded-lg border border-violet-400/20 bg-violet-950/10 p-3">
                <div className="mb-2 flex items-center justify-between">
                  <span className="text-xs font-semibold uppercase tracking-wide text-white/40">Socratic coach</span>
                  <span className="text-[10px] text-white/40">Answer in words · or right-click-drag a move to propose it</span>
                </div>
                {socraticMsgs.length === 0 && (
                  <div className="mb-2 text-sm text-white/60">
                    Stuck? Answer the coach's question out loud and it will guide you to the idea —
                    <button className="ml-1 text-violet-300 underline hover:text-violet-200" onClick={() => socraticAsk("I'm stuck — what should I be thinking about here?")}>
                      ask to start
                    </button>
                  </div>
                )}
                {socraticMsgs.length > 0 && (
                  <div className="mb-2 max-h-48 space-y-2 overflow-y-auto pr-1">
                    {socraticMsgs.map((m, i) => (
                      <div
                        key={i}
                        className={`text-sm ${m.role === "user" ? "text-right text-white/70" : "text-left text-white/90"}`}
                      >
                        <span className={`inline-block max-w-[90%] rounded-lg px-2.5 py-1.5 ${m.role === "user" ? "bg-white/10" : "bg-violet-400/10"}`}>
                          {m.content}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
                <form
                  className="flex gap-2"
                  onSubmit={(e) => { e.preventDefault(); void socraticAsk(socraticInput) }}
                >
                  <input
                    value={socraticInput}
                    onChange={(e) => setSocraticInput(e.target.value)}
                    placeholder={socraticBusy ? "Coach is thinking…" : "Your answer…"}
                    disabled={socraticBusy}
                    className="min-w-0 flex-1 rounded-md border border-white/10 bg-black/40 px-2 py-1.5 text-sm text-white/90 placeholder-white/40 focus:border-violet-400/50 focus:outline-none disabled:opacity-60"
                  />
                  <Button size="sm" type="submit" disabled={socraticBusy || !socraticInput.trim()}>
                    {socraticBusy ? "…" : "Send"}
                  </Button>
                </form>
              </div>
            </CardContent>
          </Card>

          {/* ── 2. REVIEW QUEUE (their real blunders to drill) ── */}
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
              {review.length === 0 && (
                <ActionOnboardingCard
                  id="chess-review"
                  title="Spaced Repetition"
                  description="When you blunder, the engine saves the mistake. It will re-test you on that exact position later to build muscle memory."
                  actionLabel="Analyze a Game"
                  icon={<RefreshCw size={20} />}
                  onAction={() => {
                    const el = document.getElementById("chess-analyze-btn")
                    if (el) el.click()
                  }}
                />
              )}

              {activeReview && (
                <div className="rounded-lg border border-emerald-400/20 bg-emerald-950/10 p-3">
                  <div className="mb-1 text-sm font-semibold text-emerald-200">Review position</div>
                  {leadInIndex !== null ? (
                    <div className="mb-2">
                      <div className="text-xs text-white/50 mb-2">Replaying game context ({leadInIndex + 1} of {activeReview.lead_in_moves?.length}). What led to the mistake?</div>
                      <Button size="sm" variant="secondary" onClick={playNextLeadInMove}>Play Context Move</Button>
                    </div>
                  ) : (
                    <>
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
                  {reviewSolved === "sequel" && (
                    <div className="mb-2 text-sm text-sky-300">👍 Good move! Now find the follow-up against the engine's best reply.</div>
                  )}
                  <div className="flex gap-2">
                    {reviewSolved === "none" && (
                      <Button size="sm" variant="outline" onClick={() => resolveReview(activeReview, false)}>Show answer</Button>
                    )}
                    {reviewSolved !== "none" && (
                      <Button size="sm" className="bg-emerald-600 hover:bg-emerald-500 text-white" onClick={() => {
                        if (autoAdvanceTimeoutRef.current) clearTimeout(autoAdvanceTimeoutRef.current)
                        advanceToNextMistake(activeReview.id)
                      }}>Next Mistake →</Button>
                    )}
                    <Button size="sm" variant="outline" onClick={() => { 
                      if (autoAdvanceTimeoutRef.current) clearTimeout(autoAdvanceTimeoutRef.current)
                      setActiveReview(null); setReviewSolved("none"); newGame() 
                    }}>Done reviewing</Button>
                  </div>
                  </>
                )}
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

          {/* ── 3. BLUNDER RADAR (detecting if there's a tactic) ── */}
          <Card className="border-white/10 bg-panel">
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle className="text-base">Blunder Radar</CardTitle>
                <Button size="sm" variant="outline" onClick={loadRadar}>Load Radar</Button>
              </div>
              <CardDescription>Develop your danger sense. Is there a critical move to find, or is it a solid position?</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {activeRadarItem && (
                <div className="rounded-lg border border-white/10 bg-black/20 p-4">
                  <div className="mb-3 text-sm font-medium text-white/80">Assess this position:</div>
                  {radarSolved === "none" ? (
                    <div className="flex gap-2">
                      <Button className="flex-1 bg-red-600 hover:bg-red-500 text-white" onClick={() => guessRadar(true)}>Yes, there's a tactic</Button>
                      <Button className="flex-1 bg-emerald-600 hover:bg-emerald-500 text-white" onClick={() => guessRadar(false)}>No, solid position</Button>
                    </div>
                  ) : (
                    <div className="space-y-3">
                      <div className={`text-sm ${radarSolved === "correct" ? "text-emerald-300" : "text-amber-300"}`}>
                        {radarSolved === "correct" ? "✓ Correct!" : "✗ Incorrect."}
                        {activeRadarItem.has_tactic ? " There is a tactic here." : " This is a solid position."}
                      </div>
                      {activeRadarItem.has_tactic && radarSolved === "correct" && (
                        <div className="text-xs text-white/60">Now find the move on the board!</div>
                      )}
                      <Button size="sm" variant="outline" onClick={() => { setActiveRadarItem(null); setRadarItems((prev) => prev?.filter((i) => i.id !== activeRadarItem.id) ?? []) }}>Next Position</Button>
                    </div>
                  )}
                </div>
              )}
              
              {!activeRadarItem && radarItems && radarItems.length > 0 && (
                <div className="max-h-56 space-y-1.5 overflow-y-auto">
                  {radarItems.map((r) => (
                    <button
                      key={r.id}
                      onClick={() => startRadarItem(r)}
                      className={`flex w-full items-center justify-between rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-left text-sm hover:bg-black/40`}
                    >
                      <span className="text-white/85">Radar Item</span>
                      <Badge className="border-white/20 bg-white/5 text-white/60">Evaluate</Badge>
                    </button>
                  ))}
                </div>
              )}
              {!activeRadarItem && (!radarItems || radarItems.length === 0) && (
                <div className="text-xs text-white/40">Load radar to practice sensing danger.</div>
              )}
            </CardContent>
          </Card>

          {/* ── PRACTICE POSITIONS (curated starting points — near top so a new
               learner finds them without scrolling past the games cards) ── */}
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

          <Card className="border-white/10 bg-panel">
            <CardHeader>
              <CardTitle className="text-base">Top recurring mistakes</CardTitle>
              <CardDescription>
                What you keep doing wrong, ranked — the patterns to actually drill
                (distilled from every queued mistake).
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {!topMistakes ? (
                <div className="text-xs text-white/40">Analyze games to build your mistake profile.</div>
              ) : topMistakes.length === 0 ? (
                <div className="text-xs text-white/40">No mistakes recorded yet.</div>
              ) : (
                <ol className="space-y-2.5">
                  {topMistakes.map((t, i) => (
                    <li key={i}>
                      <div className="flex items-baseline justify-between gap-2">
                        <span className="text-sm font-medium text-white/90">
                          <span className="mr-1.5 font-mono text-xs text-emerald-300">{i + 1}.</span>
                          {t.concept}
                        </span>
                        <span className="shrink-0 text-xs text-white/50">{t.share_pct}% · {t.count}×</span>
                      </div>
                      {t.severity && Object.entries(t.severity).length > 0 && (
                        <div className="mt-0.5 flex gap-1.5">
                          {Object.entries(t.severity).map(([k, v]) => (
                            <Badge key={k} className={CLASS_COLOR[k] ?? "border-white/20 bg-white/5 text-white/60"}>{k} {v}</Badge>
                          ))}
                        </div>
                      )}
                      {t.examples?.[0] && (
                        <div className="mt-1 pl-4 font-mono text-xs text-white/50">
                          you played {t.examples[0].played_san} → better {t.examples[0].best_san}
                        </div>
                      )}
                    </li>
                  ))}
                </ol>
              )}
            </CardContent>
          </Card>

          <Card className="border-white/10 bg-panel">
            <CardHeader>
              <CardTitle className="text-base">Your coach profile</CardTitle>
              <CardDescription>
                Built from your recurring mistake types — where you lose games, ranked as
                skill weaknesses. Your trainer's personal curriculum.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {!coach || coach.total === 0 ? (
                <div className="text-xs text-white/40">Analyze games to build your coach profile.</div>
              ) : (
                <>
                  {coach.focus && (
                    <div className="rounded-lg border border-emerald-400/30 bg-emerald-950/20 p-3">
                      <div className="mb-0.5 text-xs font-semibold uppercase tracking-wide text-white/40">
                        Today's focus · {coach.focus_skill}
                      </div>
                      <div className="text-sm text-emerald-100/90">{coach.focus}</div>
                    </div>
                  )}
                  {coach.skills && Object.entries(coach.skills).length > 0 && (
                    <div className="space-y-2">
                      {Object.entries(coach.skills).map(([skill, v]) => (
                        <div key={skill}>
                          <div className="mb-0.5 flex justify-between text-xs">
                            <span className="capitalize text-white/70">{skill}</span>
                            <span className="text-white/50">{v.share_pct}%</span>
                          </div>
                          <div className="h-1.5 overflow-hidden rounded-full bg-black/40">
                            <div
                              className={`h-full rounded-full ${(v.bar ?? 0) >= 80 ? "bg-red-400" : (v.bar ?? 0) >= 50 ? "bg-amber-400" : "bg-emerald-400"}`}
                              style={{ width: `${Math.max(4, Math.min(100, v.bar ?? 0))}%` }}
                            />
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                  {coach.top_concepts && coach.top_concepts.length > 0 && (
                    <div className="mt-2 text-xs text-white/50">
                      Top error: <span className="text-white/80">{coach.top_concepts[0][0]}</span> ({coach.top_concepts[0][1]}×)
                    </div>
                  )}
                </>
              )}
            </CardContent>
          </Card>

          <Card className="border-white/10 bg-panel">
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle className="text-base">Concept training</CardTitle>
                <Button id="chess-build-training-btn" size="sm" variant="outline" onClick={buildTraining} disabled={trainingBuilding}>
                  {trainingBuilding ? "…" : "Build / refresh"}
                </Button>
              </div>
              <CardDescription>
                Repair (your own mistake) → Reinforce (new position, same idea) → Transfer
                (same idea in disguise). A concept is mastered only when both Reinforce and
                Transfer are solved repeatedly.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {!trainingItem && !trainingProgress?.total_items && (
                <ActionOnboardingCard
                  id="chess-training"
                  title="Concept Training"
                  description="Repair your recurring mistakes through active recall. The trainer generates new positions that test the exact same concept you struggled with."
                  actionLabel="Build Training"
                  icon={<BookOpen size={20} />}
                  onAction={() => {
                    const el = document.getElementById("chess-build-training-btn")
                    if (el) el.click()
                  }}
                />
              )}

              {/* Confidence calibration summary (analytics only — never reorders
                  training; observed performance outranks self-reported confidence). */}
              {trainingCalibration && trainingCalibration.concepts && Object.keys(trainingCalibration.concepts).length > 0 && (
                <div className="rounded-lg border border-white/10 bg-black/20 p-2 text-xs">
                  {Object.entries(trainingCalibration.concepts)
                    .filter(([, v]: any) => v.overconfident)
                    .slice(0, 3)
                    .map(([c]) => (
                      <div key={c} className="text-amber-300/90">
                        ⚠ You're often <span className="font-semibold">overconfident</span> on <span className="capitalize">{c}</span> — slow down and verify.
                      </div>
                    ))}
                  {!Object.values(trainingCalibration.concepts).some((v: any) => v.overconfident) && (
                    <div className="text-white/40">Confidence calibration is building as you train.</div>
                  )}
                </div>
              )}

              {!trainingItem && (
                <div className="flex flex-col gap-2">
                  <div className="text-xs text-white/40">
                    {trainingProgress?.total_items ? `${trainingProgress.total_items} training items ready.` : "No training items yet — analyze games, then Build."}
                  </div>
                  {trainingProgress && trainingProgress.concepts && Object.keys(trainingProgress.concepts).length > 0 && (
                    <div className="space-y-1">
                      {Object.entries(trainingProgress.concepts)
                        .filter(([, v]) => !v.concept_mastered)
                        .slice(0, 4)
                        .map(([c, v]) => (
                          <div key={c} className="flex items-center justify-between text-xs">
                            <span className="capitalize text-white/70">{c}</span>
                            <span className="text-white/40">
                              {v.reinforce_mastered ? "reinforced" : `${v.reinforce ?? 0}+`} · {v.transfer_mastered ? "transferred" : `${v.transfer ?? 0}T`}
                              {v.mastery === "mastered" && " · mastered"}
                            </span>
                          </div>
                        ))}
                    </div>
                  )}
                  <Button size="sm" onClick={buildTraining} disabled={trainingBuilding}>
                    {trainingBuilding ? "Building…" : "Build training items"}
                  </Button>
                </div>
              )}

              {trainingItem && trainingItem.id && trainingItem.pre_fen && (
                <div className="rounded-lg border border-emerald-400/20 bg-emerald-950/10 p-3">
                  <div className="mb-1 flex items-center justify-between">
                    <span className="text-sm font-semibold capitalize text-emerald-200">{trainingItem.concept}</span>
                    <Badge className="border-white/20 bg-white/5 text-white/60">
                      {trainingItem.stage} {trainingItem.difficulty ? `· diff ${trainingItem.difficulty}` : ""}
                    </Badge>
                  </div>
                  <div className="mb-2 text-sm text-white/85">{trainingItem.prompt}</div>
                  <div className="mb-2">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => {
                        if (trainingItem?.pre_fen) {
                          setFen(trainingItem.pre_fen)
                          setFenHistory([])
                          setHistory([])
                          setResult(null)
                          setLastMove(null)
                          setSelected(null)
                          setLegalTargets([])
                          setRetryFen(null)
                        }
                      }}
                    >
                      Show on board
                    </Button>
                  </div>
                  {trainingAnswered ? (
                    <div className={`mt-2 text-sm ${trainingAnswered.correct ? "text-emerald-300" : "text-amber-300"}`}>
                      {trainingAnswered.correct
                        ? trainingAnswered.mastered
                          ? `✓ Correct — ${trainingItem.solution_san}, mastered! Loading the next item…`
                          : `✓ Correct — ${trainingItem.solution_san}, box advanced. Loading the next item…`
                        : `✗ Not quite — the answer was ${trainingItem.solution_san}. It will come back soon.`}
                    </div>
                  ) : (
                    <>
                      {/* Confidence is captured BEFORE the answer is revealed. */}
                      <div className="mb-1 mt-1 text-[10px] text-white/40">How confident are you? (choose before you check)</div>
                      <div className="mb-2 flex gap-1.5">
                        {(["guess", "idea", "confident"] as const).map((c) => (
                          <button
                            key={c}
                            onClick={() => { setTrainingConfidence(c); setTrainingConfidenceAt(Date.now() / 1000) }}
                            className={`rounded px-2 py-1 text-[11px] ${trainingConfidence === c ? "bg-emerald-400/30 text-emerald-200" : "bg-black/30 text-white/60 hover:text-white/90"}`}
                          >
                            {c === "guess" ? "🔴 Guessing" : c === "idea" ? "🟡 I have an idea" : "🟢 Confident"}
                          </button>
                        ))}
                      </div>
                      <div className="mt-1 flex gap-1.5">
                        <Button size="sm" variant="outline" onClick={() => answerTraining(true)} disabled={!trainingConfidence}>✓ I got it</Button>
                        <Button size="sm" variant="outline" onClick={() => answerTraining(false)} disabled={!trainingConfidence}>✗ Not quite</Button>
                      </div>
                    </>
                  )}
                </div>
              )}
            </CardContent>
          </Card>

          {/* ── 7. HANGING PIECES DRILL (tactical vision) ── */}
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

          {/* ── 3. MY CHESS.COM GAMES (800+ real games — most valuable data source) ── */}
          <Card className="border-white/10 bg-panel">
            <CardHeader>
              <CardTitle className="text-base">My chess.com games</CardTitle>
              <CardDescription>
                Import ALL your real games (public API, no login) so the trainer knows your actual
                weaknesses. Build your profile + journey instantly, then run the background engine
                analysis to queue every blunder into your spaced practice.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex gap-2">
                <input
                  className="flex-1 rounded-md border border-white/10 bg-black/40 px-3 py-1.5 text-sm"
                  placeholder="chess.com username"
                  value={ccUsername}
                  onChange={(e) => saveCcUsername(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && buildProfile()}
                />
                <Button size="sm" variant="outline" onClick={buildProfile} disabled={ccProfileLoading}>
                  {ccProfileLoading ? "Building…" : "Profile + journey"}
                </Button>
                <Button id="chess-analyze-btn" size="sm" variant="outline" onClick={startAnalysis} disabled={ccJob?.status === "running"}>
                  {ccJob?.status === "running" ? "Analyzing…" : "Analyze all games"}
                </Button>
              </div>

              {ccJob?.status === "running" && (
                <div className="rounded-lg border border-sky-400/30 bg-sky-950/10 p-3 text-sm text-sky-200">
                  <div className="font-semibold">Background analysis running</div>
                  <div className="text-xs text-white/70">
                    {ccJob.done_games ?? 0} / {ccJob.total_games ?? "?"} games · {ccJob.mistakes_queued ?? 0} mistakes
                    queued so far. Leave the computer on — it resumes if you reboot.
                  </div>
                </div>
              )}
              {ccJob?.status === "done" && (
                <div className="rounded-lg border border-emerald-400/30 bg-emerald-950/10 p-3 text-sm text-emerald-200">
                  Done — {ccJob.done_games} games analyzed, {ccJob.mistakes_queued} mistakes queued for review.
                </div>
              )}
              {ccJob?.error && <div className="text-xs text-red-300">{ccJob.error}</div>}

              {ccProfile?.error && <div className="text-xs text-red-300">{ccProfile.error}</div>}

              {ccProfile?.ok && (
                <div className="space-y-3">
                  <div className="flex flex-wrap gap-2">
                    <Badge className="border-white/20 bg-white/5 text-white/70">{ccProfile.games} games</Badge>
                    {ccProfile.record && (
                      <Badge className="border-emerald-400/30 bg-emerald-400/5 text-emerald-300">
                        {ccProfile.record.wins}W {ccProfile.record.draws}D {ccProfile.record.losses}L
                      </Badge>
                    )}
                    {ccProfile.journey_summary?.peak != null && (
                      <Badge className="border-white/20 bg-white/5 text-white/70">
                        peak {ccProfile.journey_summary.peak} → now {ccProfile.journey_summary.current}
                      </Badge>
                    )}
                  </div>

                  {ccProfile.journey && ccProfile.journey.length > 0 && (
                    <div>
                      <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-white/40">
                        Your journey (rating per game)
                      </div>
                      <div className="flex h-20 items-end gap-px overflow-hidden rounded-md bg-black/40 p-1">
                        {ccProfile.journey.map((p, i) => {
                          const r = p.rating ?? 0
                          const min = ccProfile.journey_summary?.trough ?? r
                          const max = ccProfile.journey_summary?.peak ?? r
                          const h = max > min ? ((r - min) / (max - min)) * 100 : 50
                          const win = p.result === "win"
                          return (
                            <div
                              key={i}
                              title={`${p.result} · ${r}`}
                              className="flex-1 rounded-sm"
                              style={{
                                height: `${Math.max(3, Math.min(100, h))}%`,
                                backgroundColor: win ? "#3fae6a" : p.result === "loss" ? "#d2554d" : "#4a6fa5",
                              }}
                            />
                          )
                        })}
                      </div>
                      {ccProfile.journey_summary && (
                        <div className="mt-1 text-[10px] text-white/40">
                          {ccProfile.journey_summary.first} first · peak {ccProfile.journey_summary.peak} · trough{" "}
                          {ccProfile.journey_summary.trough} · best climb +{ccProfile.journey_summary.best_gain} · worst
                          drop {ccProfile.journey_summary.worst_drop}
                        </div>
                      )}
                    </div>
                  )}

                  {ccProfile.think_seconds && (
                    <div className="text-xs text-white/60">
                      Avg think (s): opening {ccProfile.think_seconds.opening ?? "—"} · middlegame{" "}
                      {ccProfile.think_seconds.middlegame ?? "—"} · endgame {ccProfile.think_seconds.endgame ?? "—"}
                    </div>
                  )}

                  {ccProfile.opening_report && ccProfile.opening_report.length > 0 && (
                    <div>
                      <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-white/40">
                        Openings (most played)
                      </div>
                      <ul className="space-y-0.5 text-xs text-white/60">
                        {ccProfile.opening_report.slice(0, 5).map((o, i) => (
                          <li key={i} className="flex justify-between gap-2">
                            <span className="truncate font-mono">{o.opening}</span>
                            <span className="shrink-0">{o.games}g · {o.score_pct}%</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}
            </CardContent>
          </Card>

          <Card className="border-white/10 bg-panel">
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle className="text-base">Game review</CardTitle>
                {gameReview?.accuracy != null && (
                  <Badge className="border-emerald-400/40 bg-emerald-400/10 text-emerald-300">{gameReview.accuracy}%</Badge>
                )}
              </div>
              <CardDescription>
                Record a full game, then get the guided review — eval curve, key moments, and one-click
                queue of every mistake into your spaced practice.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex flex-wrap gap-2">
                <Button size="sm" variant={gameId ? "destructive" : "outline"} onClick={startGame}>
                  {gameId ? "New recorded game" : "Start recorded game"}
                </Button>
                {gameId && (
                  <Button size="sm" variant="outline" onClick={finishReview} disabled={reviewing}>
                    {reviewing ? "Reviewing…" : "Finish + review game"}
                  </Button>
                )}
              </div>

              {gameReview?.error && <div className="text-xs text-red-300">{gameReview.error}</div>}

              {gameReview?.ok && (
                <div className="space-y-2">
                  {gameReview.move_count != null && (
                    <div className="text-xs text-white/50">{gameReview.move_count} moves</div>
                  )}
                  {gameReview.phases && Object.keys(gameReview.phases).length > 0 && (
                    <div className="flex flex-wrap gap-1.5">
                      {Object.entries(gameReview.phases).map(([phase, acc]) => (
                        <Badge key={phase} className="border-white/20 bg-white/5 text-white/70">
                          {phase} {acc}%
                        </Badge>
                      ))}
                    </div>
                  )}
                  {gameReview.curve && gameReview.curve.length > 0 && (
                    <div>
                      <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-white/40">Eval curve</div>
                      <EvalCurveChart curve={gameReview.curve} />
                    </div>
                  )}
                  {gameReview.key_moments && gameReview.key_moments.length > 0 && (
                    <div>
                      <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-white/40">Key moments</div>
                      <ul className="space-y-1 text-xs">
                        {gameReview.key_moments.map((k, i) => (
                          <li key={i} className="text-white/70">
                            <span className={k.classification === "Blunder" ? "text-red-300" : "text-emerald-300"}>
                              {k.move_n}. {k.san}
                            </span>{" "}
                            {k.classification}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {gameReview.queue_mistakes && gameReview.queue_mistakes.length > 0 && (
                    <Button size="sm" variant="outline" onClick={queueGameMistakes}>
                      Queue {gameReview.queue_mistakes.length} mistake(s) for spaced review
                    </Button>
                  )}
                </div>
              )}
            </CardContent>
          </Card>

          <Card className="border-white/10 bg-panel">
            <CardHeader>
              <div className="flex items-center justify-between gap-2">
                <CardTitle className="text-base">Play like the greats</CardTitle>
                <div className="flex overflow-hidden rounded-md border border-white/10">
                  <button
                    onClick={() => setGmTrainMode(true)}
                    className={`px-2 py-1 text-[11px] ${gmTrainMode ? "bg-emerald-400/20 text-emerald-300" : "text-white/50 hover:text-white/80"}`}
                  >
                    Train
                  </button>
                  <button
                    onClick={() => setGmTrainMode(false)}
                    className={`px-2 py-1 text-[11px] ${!gmTrainMode ? "bg-emerald-400/20 text-emerald-300" : "text-white/50 hover:text-white/80"}`}
                  >
                    Explore
                  </button>
                </div>
              </div>
              <CardDescription>
                {gmTrainMode
                  ? "Train mode: at key moments you think before the GM's move is revealed — then the 'why' lands harder."
                  : "Explore mode: smooth playback, every move explained."}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="grid gap-1.5 sm:grid-cols-2">
                {gmGames.map((g) => (
                  <button
                    key={g.id}
                    onClick={() => startGm(g.id)}
                    className="rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-left text-xs hover:bg-black/40"
                  >
                    <div className="font-semibold text-white/90">{g.name}</div>
                    <div className="text-white/50">{g.player} · {g.year}</div>
                  </button>
                ))}
              </div>

              {gmStudy?.ok && !gmStudy.finished && gmStudy.fen_before && (
                <div className="rounded-lg border border-emerald-400/20 bg-emerald-950/10 p-3">
                  <div className="mb-1 text-sm font-semibold text-emerald-200">{gmStudy.name}</div>
                  <div className="mb-2 text-xs text-white/60">
                    Move {gmStudy.move_number} · {gmStudy.side_to_move} to move
                    {gmStudy.total_plies != null && gmStudy.ply != null ? ` · ${gmStudy.ply + 1}/${gmStudy.total_plies} plies` : ""}
                    {gmStudy.is_key_moment && gmTrainMode && !gmRevealed && (
                      <span className="ml-2 rounded bg-amber-400/20 px-1.5 py-0.5 text-[10px] font-semibold text-amber-300">
                        KEY MOMENT
                      </span>
                    )}
                  </div>

                  {/* THINK POSITION (Train mode, not yet revealed): the move is hidden. */}
                  {gmTrainMode && gmStudy.think_required && !gmRevealed ? (
                    <div className="rounded-lg border border-amber-400/30 bg-amber-950/10 p-3">
                      <div className="mb-1 text-sm font-semibold text-amber-200">Your move — what would you play?</div>
                      {gmStudy.hint && <div className="mb-2 text-xs text-amber-100/80">{gmStudy.hint}</div>}
                      <div className="mb-2 text-[10px] text-white/40">
                        Think on the board. Then say how confident you feel and reveal the GM's move.
                      </div>
                      <div className="mb-2 flex gap-1.5">
                        {(["guess", "idea", "confident"] as const).map((c) => (
                          <button
                            key={c}
                            onClick={() => setGmConfidence(c)}
                            className={`rounded px-2 py-1 text-[11px] ${gmConfidence === c ? "bg-emerald-400/30 text-emerald-200" : "bg-black/30 text-white/60 hover:text-white/90"}`}
                          >
                            {c === "guess" ? "🔴 Guessing" : c === "idea" ? "🟡 I have an idea" : "🟢 Confident"}
                          </button>
                        ))}
                      </div>
                      <Button size="sm" onClick={gmReveal}>Reveal the GM's move</Button>
                    </div>
                  ) : (
                    <>
                      {/* REVEALED: the GM move + explanation. */}
                      <div className="mb-1 text-sm text-white/85">
                        The GM played <span className="font-mono font-semibold text-emerald-300">{gmStudy.gm_move_san}</span>
                        {gmConfidence && gmStudy.think_required && (
                          <span className="ml-2 text-[11px] text-white/40">
                            (you were {gmConfidence === "confident" ? "confident" : gmConfidence === "idea" ? "unsure" : "guessing"})
                          </span>
                        )}
                      </div>
                      {gmStudy.explanation && (
                        <div className="rounded-lg border border-violet-400/20 bg-violet-950/10 p-2 text-xs leading-relaxed text-violet-100/90">
                          <div className="mb-1 font-semibold uppercase tracking-wide text-white/40">
                            Why {gmStudy.gm_move_san} — what it threatens, what it sets up
                          </div>
                          <pre className="whitespace-pre-wrap">{gmStudy.explanation}</pre>
                          {gmStudy.degraded && (
                            <div className="mt-1 text-[10px] text-white/40">(deterministic engine explanation — LLM unavailable)</div>
                          )}
                        </div>
                      )}
                    </>
                  )}
                  <Button size="sm" className="mt-3" onClick={gmNext} disabled={gmStudy.loading}>
                    {gmStudy.loading ? "…" : "Next move →"}
                  </Button>
                </div>
              )}

              {gmStudy?.finished && (
                <div className="text-sm text-emerald-300">Game complete — {gmStudy.result}. Pick another to keep going.</div>
              )}

              <div className="rounded-lg border border-white/10 bg-black/20 p-3 text-xs leading-relaxed text-white/60">
                <div className="mb-1 font-semibold uppercase tracking-wide text-white/40">Fischer → Carlsen: how the game changed</div>
                <p><span className="text-white/80">Bobby Fischer</span> (sharpest attacker of his era) showed the power of concrete, forcing play — look at how he wins material or mates.</p>
                <p className="mt-1"><span className="text-white/80">Magnus Carlsen</span> (world #1) wins by outplaying in quiet positions — tiny advantages, endgame precision, and squeezing a draw into a win. Compare how each world champion wins the same type of position.</p>
              </div>
            </CardContent>
          </Card>

          <Card className="border-white/10 bg-panel">
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle className="text-base">Progress</CardTitle>
                {analytics?.training_rating != null && (
                  <Badge className="border-emerald-400/40 bg-emerald-400/10 text-emerald-300">Training ~{analytics.training_rating}</Badge>
                )}
              </div>
              <CardDescription>
                Your move-quality signals across recorded games (vs the engine — an estimate, not Elo).
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {analytics && (analytics.games_count ?? 0) === 0 && (
                <div className="text-xs text-white/40">Play and finish a recorded game to see your progress.</div>
              )}
              {analytics?.skills && (analytics.games_count ?? 0) > 0 && (
                <div className="space-y-2">
                  <div className="text-xs text-white/50">
                    {analytics.games_count} game(s) · {analytics.moves_count} moves
                  </div>
                  {Object.entries(analytics.skills).map(([k, v]) => (
                    <div key={k}>
                      <div className="mb-0.5 flex justify-between text-xs">
                        <span className="capitalize text-white/70">{k.replace("_", " ")}</span>
                        <span className="text-white/50">{v}%</span>
                      </div>
                      <div className="h-1.5 overflow-hidden rounded-full bg-black/40">
                        <div
                          className={`h-full rounded-full ${v >= 80 ? "bg-emerald-400" : v >= 50 ? "bg-amber-400" : "bg-red-400"}`}
                          style={{ width: `${Math.max(0, Math.min(100, v))}%` }}
                        />
                      </div>
                    </div>
                  ))}
                  {analytics.recent && analytics.recent.length > 0 && (
                    <div>
                      <div className="mb-1 mt-2 text-xs font-semibold uppercase tracking-wide text-white/40">Recent games</div>
                      <ul className="space-y-0.5 text-xs text-white/60">
                        {analytics.recent.map((r) => (
                          <li key={r.game_id}>{r.move_count} moves · {r.accuracy}% accuracy</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}
            </CardContent>
          </Card>

          <Card className="border-white/10 bg-panel">
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle className="text-base">Tips from the books</CardTitle>
                <Button size="sm" variant="outline" onClick={loadTips} disabled={tipsLoading}>
                  {tipsLoading ? "…" : "New tips"}
                </Button>
              </div>
              <CardDescription>
                Ten book-grounded tips for a ~500 player, fresh every time the trainer loads.
              </CardDescription>
            </CardHeader>
            <CardContent>
              {tipsLoading && !tips ? (
                <div className="text-xs text-white/40">Loading tips…</div>
              ) : tips && tips.length > 0 ? (
                <ol className="space-y-2.5">
                  {tips.map((t, i) => (
                    <li key={i} className="text-sm text-white/85">
                      <span className="mr-1.5 font-mono text-xs text-emerald-300">{i + 1}.</span>
                      {t.tip}
                      <div className="mt-0.5 pl-5 text-xs text-white/40">— {t.source}</div>
                    </li>
                  ))}
                </ol>
              ) : (
                <div className="text-xs text-white/40">Tips unavailable right now.</div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}
