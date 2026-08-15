/**
 * SOTA chess board renderer (2026 lichess-style): wood-toned squares, crisp
 * SVG piece set, coordinates, last-move highlight, check glow, selected-square
 * ring, legal-move dots, piece-move animation via CSS grid positioning, and a
 * win%-split eval bar. Pure React + CSS — no board dependency.
 */
import { useMemo } from "react"
import { ChessPiece } from "./ChessPiece"

const FILES = "abcdefgh"
const RANKS = "87654321"

const LIGHT = "#f0d9b5"
const DARK = "#b58863"
const LIGHT_HOVER = "#f7e7c7"
const DARK_HOVER = "#c9a277"
const LAST_MOVE = "rgba(255, 213, 94, 0.5)"
const SELECTED = "rgba(20, 85, 30, 0.6)"
const CHECK = "radial-gradient(circle, rgba(255,0,0,0.7) 18%, rgba(255,0,0,0.25) 60%)"
const DOT = "radial-gradient(circle, rgba(0,0,0,0.22) 0%, rgba(0,0,0,0.22) 34%, transparent 40%)"
const CAPTURE_RING = "radial-gradient(circle, transparent 0%, transparent 58%, rgba(0,0,0,0.25) 62%, rgba(0,0,0,0.25) 72%, transparent 76%)"

function parseFen(fen: string): Record<string, string> {
  const board: Record<string, string> = {}
  const placement = fen.split(" ")[0]
  const rows = placement.split("/")
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
  return board
}

function squareColor(sq: string): "light" | "dark" {
  const file = FILES.indexOf(sq[0])
  const rank = RANKS.indexOf(sq[1])
  return (file + rank) % 2 === 1 ? "dark" : "light"
}

export type BoardHighlights = {
  lastMove?: { from: string; to: string } | null
  selected?: string | null
  legalTargets?: string[]
  checkSquare?: string | null
  arrows?: Array<{ from: string; to: string; color?: string }>
}

type Props = {
  fen: string
  interactive?: boolean
  onSquareClick?: (sq: string) => void
  orientation?: "white" | "black"
  highlights?: BoardHighlights
  evalBar?: { whitePct: number } | null
}

export default function ChessBoard({
  fen,
  interactive = false,
  onSquareClick,
  orientation = "white",
  highlights = {},
  evalBar = null,
}: Props) {
  const board = useMemo(() => parseFen(fen), [fen])
  const { lastMove, selected, legalTargets = [], checkSquare, arrows = [] } = highlights

  // Build the displayed square order honoring orientation.
  const displayFiles = orientation === "white" ? FILES.split("") : FILES.split("").reverse()
  const displayRanks = orientation === "white" ? RANKS.split("") : RANKS.split("")

  const svgArrows = useMemo(() => {
    if (!arrows.length) return null
    const sqToCoord = (sq: string) => {
      const f = displayFiles.indexOf(sq[0])
      const r = displayRanks.indexOf(sq[1])
      return { x: f * 12.5 + 6.25, y: r * 12.5 + 6.25 }
    }
    return (
      <svg viewBox="0 0 100 100" className="pointer-events-none absolute inset-0 h-full w-full">
        {arrows.map((a, i) => {
          const from = sqToCoord(a.from)
          const to = sqToCoord(a.to)
          const color = a.color ?? "rgba(20,170,110,0.9)"
          const dx = to.x - from.x
          const dy = to.y - from.y
          const len = Math.hypot(dx, dy) || 1
          const ux = dx / len, uy = dy / len
          const startX = from.x + ux * 8
          const startY = from.y + uy * 8
          const endX = to.x - ux * 7
          const endY = to.y - uy * 7
          const head = 5
          const hx = ux * head, hy = uy * head
          return (
            <g key={i}>
              <path
                d={`M${startX} ${startY} L${endX} ${endY}`}
                stroke={color} strokeWidth="3.4" strokeLinecap="round" fill="none" opacity="0.95"
              />
              <path
                d={`M${endX} ${endY} L${endX - hx + hy * 0.6} ${endY - hy - hx * 0.6} L${endX - hx - hy * 0.6} ${endY - hy + hx * 0.6} Z`}
                fill={color}
              />
            </g>
          )
        })}
      </svg>
    )
  }, [arrows, displayFiles, displayRanks])

  const whitePct = evalBar ? Math.max(0, Math.min(100, evalBar.whitePct)) : null

  return (
    <div className="flex w-full gap-2">
      {evalBar && whitePct !== null && (
        <div className="flex w-4 shrink-0 flex-col overflow-hidden rounded-md border border-white/20 bg-black/60" style={{ height: "100%", aspectRatio: "1 / 1" }}>
          <div className="flex flex-col" style={{ height: "100%" }}>
            <div className="bg-slate-100 transition-all duration-500" style={{ height: `${whitePct}%` }} />
            <div className="flex-1 bg-slate-900" />
          </div>
        </div>
      )}
      <div
        className="relative w-full select-none overflow-hidden rounded-md border border-white/25 shadow-2xl"
        style={{ aspectRatio: "1 / 1", background: DARK }}
      >
        <div className="grid h-full w-full grid-cols-8 grid-rows-8">
          {displayRanks.map((rank) =>
            displayFiles.map((file) => {
              const sq = file + rank
              const piece = board[sq]
              const dark = squareColor(sq) === "dark"
              const isLast = lastMove && (lastMove.from === sq || lastMove.to === sq)
              const isSelected = selected === sq
              const isLegalTarget = legalTargets.includes(sq)
              const isCheck = checkSquare === sq
              const hasCapture = isLegalTarget && !!piece
              const base = dark ? DARK : LIGHT
              const hover = dark ? DARK_HOVER : LIGHT_HOVER
              return (
                <button
                  key={sq}
                  onClick={() => interactive && onSquareClick?.(sq)}
                  className="relative flex items-center justify-center transition-[background-color] duration-100"
                  style={{
                    backgroundColor: isLast ? LAST_MOVE : isSelected ? SELECTED : base,
                    backgroundImage: isCheck ? CHECK : undefined,
                    cursor: interactive ? (isLegalTarget ? "pointer" : piece ? "pointer" : "default") : "default",
                  }}
                  onMouseEnter={(e) => { if (interactive && !isLast && !isSelected) e.currentTarget.style.backgroundColor = hover }}
                  onMouseLeave={(e) => { if (interactive && !isLast && !isSelected) e.currentTarget.style.backgroundColor = base }}
                  aria-label={sq}
                >
                  {isLegalTarget && !hasCapture && <span className="pointer-events-none absolute inset-0" style={{ backgroundImage: DOT }} />}
                  {isLegalTarget && hasCapture && <span className="pointer-events-none absolute inset-0" style={{ backgroundImage: CAPTURE_RING }} />}
                  {piece && (
                    <span className="relative z-10 flex h-full w-full items-center justify-center transition-transform duration-150 hover:scale-[1.06]" style={{ padding: "4%" }}>
                      <ChessPiece piece={piece} />
                    </span>
                  )}
                  {file === "a" && (
                    <span className="absolute top-0 left-0.5 z-20 text-[9px] font-bold leading-tight text-black/55">{rank}</span>
                  )}
                  {rank === "8" && (
                    <span className="absolute right-0.5 bottom-0 z-20 text-[9px] font-bold leading-tight text-black/55">{file}</span>
                  )}
                </button>
              )
            })
          )}
        </div>
        {svgArrows}
      </div>
    </div>
  )
}
