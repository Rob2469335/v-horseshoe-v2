import { useCallback, useEffect, useMemo, useRef, useState } from "react"
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
  const [gameId, setGameId] = useState<string | null>(null)
  const [gameReview, setGameReview] = useState<GameReview | null>(null)
  const [reviewing, setReviewing] = useState(false)
  const [reviewSolved, setReviewSolved] = useState<"none" | "solved" | "failed">("none")
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

  const board = useMemo(() => parseFen(fen), [fen])
  const turn = useMemo(() => sideToMove(fen), [fen])

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
        body: JSON.stringify({ fen: preFen, uci, rating, want_explain: true, game_id: gameId }),
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
  const [ccPoll, setCcPoll] = useState<NodeJS.Timeout | null>(null)

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
  useEffect(() => { if (trainingItem) setFen(trainingItem.pre_fen ?? fen) }, [trainingItem]) // eslint-disable-line react-hooks/exhaustive-deps

  const [analytics, setAnalytics] = useState<Analytics | null>(null)
  const loadAnalytics = useCallback(async () => {
    try {
      const a = await (await fetch(`${backendUrl}/chess/trainer/analytics`)).json() as Analytics
      setAnalytics(a)
    } catch { /* ignore */ }
  }, [backendUrl])
  useEffect(() => { loadAnalytics() }, [loadAnalytics])

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

      <div className="grid gap-6 lg:grid-cols-[minmax(0,520px)_minmax(0,1fr)] lg:items-start">
        {/* Board + controls — sticky so the board stays visible while you scroll
            the right-side lists (review queue, chess.com games, GM games, etc.). */}
        <Card className="border-white/10 bg-panel lg:sticky lg:top-4 lg:self-start">
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
                <Button size="sm" variant="outline" onClick={buildTraining} disabled={trainingBuilding}>
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
                <Button size="sm" variant="outline" onClick={startAnalysis} disabled={ccJob?.status === "running"}>
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
                      <div className="flex h-16 items-end gap-px overflow-hidden rounded-md bg-black/40 p-1">
                        {gameReview.curve.map((pt) => (
                          <div
                            key={pt.n}
                            title={`${pt.n}. ${pt.san ?? ""} (${pt.win_pct ?? "?"}%)`}
                            className="flex-1 rounded-sm transition-all"
                            style={{
                              height: `${Math.max(4, Math.min(100, pt.win_pct ?? 50))}%`,
                              backgroundColor: (pt.classification ?? "").startsWith("B") ? "#e05b4d" : "#4da3e0",
                            }}
                          />
                        ))}
                      </div>
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
