/**
 * SOTA chess board renderer (2026 — chessground/lichess recipe).
 *
 * Key details (from chessground's base CSS + lichess board gen, not invented):
 *  - Real cburnett pieces rendered as background-image, 100% of the square.
 *  - Flat two-tone board (brown theme): #f0d9b5 / #b58863.
 *  - Last move: muted olive wash rgba(155,199,0,0.41) (NOT yellow).
 *  - Selected square: translucent green rgba(20,85,30,0.5).
 *  - Move-dest dot: green, radius 22%, crisp #208530 ring.
 *  - Capture target: ring at 80%.
 *  - Check: red radial ellipse fading out by 89%.
 *  - Coordinates: lichess-style, 9px, opacity .8, alternating dark/light.
 *  - Move animation: transform transition ~200ms cubic-bezier on pieces.
 */
import { useMemo } from "react"
import { ChessPiece } from "./ChessPiece"

const FILES = "abcdefgh"
const RANKS = "87654321"

// Vibrant board themes — rich, saturated two-tone palettes (2026 look: vivid
// wood/emerald/ocean/royal/violet). The lichess flat recipe, but colorful.
export type BoardThemeKey = "vibrant" | "emerald" | "ocean" | "royal" | "violet" | "rose" | "amber"

export const BOARD_THEMES: Record<BoardThemeKey, { light: string; dark: string; name: string }> = {
  vibrant: { light: "#f2e6b0", dark: "#4f8a3c", name: "Vibrant" },
  emerald: { light: "#e8f5e0", dark: "#3f8a4c", name: "Emerald" },
  ocean: { light: "#d8ecf5", dark: "#2f7f9e", name: "Ocean" },
  royal: { light: "#dce4f7", dark: "#3a5fc9", name: "Royal" },
  violet: { light: "#efe3fa", dark: "#7a5cc2", name: "Violet" },
  rose: { light: "#fde8ec", dark: "#c2546f", name: "Rose" },
  amber: { light: "#fff3d6", dark: "#c98a2d", name: "Amber" },
}

function themeOf(key: BoardThemeKey | string): { light: string; dark: string } {
  return BOARD_THEMES[key as BoardThemeKey] ?? BOARD_THEMES.vibrant
}

const LAST_MOVE = "rgba(255, 213, 94, 0.55)"
const SELECTED = "rgba(20, 85, 30, 0.5)"
const HOVER = "rgba(20, 85, 30, 0.3)"
const CHECK = "radial-gradient(ellipse at center, rgba(255,0,0,1) 0%, rgba(231,0,0,1) 25%, rgba(169,0,0,0) 89%, rgba(158,0,0,0) 100%)"
const DEST_DOT = "radial-gradient(rgba(20,85,30,0.5) 22%, #208530 0, rgba(0,0,0,0.3) 0, rgba(0,0,0,0) 0)"
const DEST_RING = "radial-gradient(transparent 0%, transparent 80%, rgba(20,85,0,0.3) 80%)"

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
  return (FILES.indexOf(sq[0]) + RANKS.indexOf(sq[1])) % 2 === 1 ? "dark" : "light"
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
  theme?: BoardThemeKey | string
}

export default function ChessBoard({
  fen,
  interactive = false,
  onSquareClick,
  orientation = "white",
  highlights = {},
  evalBar = null,
  theme = "vibrant",
}: Props) {
  const board = useMemo(() => parseFen(fen), [fen])
  const { lastMove, selected, legalTargets = [], checkSquare, arrows = [] } = highlights
  const { light: LIGHT, dark: DARK } = themeOf(theme)

  const displayFiles = orientation === "white" ? FILES.split("") : FILES.split("").reverse()
  const displayRanks = orientation === "white" ? RANKS.split("") : RANKS.split("")

  const whitePct = evalBar ? Math.max(0, Math.min(100, evalBar.whitePct)) : null

  // Piece animation layer: each piece is absolutely positioned at its square
  // and animates via a transform transition (the 2026 FLIP-style standard).
  // We key by square so a moved piece is the same DOM element translated.
  const pieceLayer = useMemo(() => {
    const items: Array<{ sq: string; piece: string; fileIdx: number; rankIdx: number }> = []
    for (let fi = 0; fi < 8; fi++) {
      for (let ri = 0; ri < 8; ri++) {
        const sq = displayFiles[fi] + displayRanks[ri]
        const p = board[sq]
        if (p) items.push({ sq, piece: p, fileIdx: fi, rankIdx: ri })
      }
    }
    return items
    // displayFiles/displayRanks/board are stable per render; recompute on change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [board, displayFiles.join(""), displayRanks.join("")])

  const sqTopPct = (rankIdx: number) => rankIdx * 12.5
  const sqLeftPct = (fileIdx: number) => fileIdx * 12.5

  return (
    <div className="flex w-full gap-2">
      {evalBar && whitePct !== null && (
        <div className="w-4 shrink-0 overflow-hidden rounded-md border border-black/40" style={{ aspectRatio: "1 / 1", background: "#222" }}>
          <div className="flex h-full flex-col">
            <div className="bg-[#eee] transition-all duration-500" style={{ height: `${whitePct}%` }} />
            <div className="flex-1 bg-[#111]" />
          </div>
        </div>
      )}
      <div
        className="relative w-full select-none overflow-hidden rounded-[4px] border border-black/50 shadow-[0_4px_12px_rgba(0,0,0,0.55)]"
        style={{ aspectRatio: "1 / 1", background: DARK }}
      >
        <div className="grid h-full w-full grid-cols-8 grid-rows-8">
          {displayRanks.map((rank) =>
            displayFiles.map((file) => {
              const sq = file + rank
              const piece = board[sq]
              const dark = squareColor(sq) === "dark"
              const base = dark ? DARK : LIGHT
              const isLast = lastMove && (lastMove.from === sq || lastMove.to === sq)
              const isSelected = selected === sq
              const isDest = legalTargets.includes(sq)
              const isCheck = checkSquare === sq
              const hasCapture = isDest && !!piece
              const coordColor = dark ? "rgba(255,255,255,0.8)" : "rgba(72,72,72,0.8)"

              return (
                <button
                  key={sq}
                  onClick={() => interactive && onSquareClick?.(sq)}
                  className="relative"
                  style={{
                    backgroundColor: isSelected ? SELECTED : isLast ? LAST_MOVE : base,
                    backgroundImage: isCheck ? CHECK : undefined,
                    cursor: interactive ? "pointer" : "default",
                  }}
                  onMouseEnter={(e) => { if (interactive && isDest) e.currentTarget.style.backgroundColor = HOVER }}
                  onMouseLeave={(e) => { if (interactive && isDest) e.currentTarget.style.backgroundColor = isSelected ? SELECTED : isLast ? LAST_MOVE : base }}
                  aria-label={sq}
                >
                  {isDest && !hasCapture && (
                    <span className="pointer-events-none absolute inset-0" style={{ backgroundImage: DEST_DOT }} />
                  )}
                  {isDest && hasCapture && (
                    <span className="pointer-events-none absolute inset-0" style={{ backgroundImage: DEST_RING }} />
                  )}
                  {file === "a" && (
                    <span className="absolute top-0 left-0.5 text-[9px] font-semibold leading-tight" style={{ color: coordColor }}>{rank}</span>
                  )}
                  {rank === "8" && (
                    <span className="absolute right-0.5 bottom-0 text-[9px] font-semibold uppercase leading-tight" style={{ color: coordColor }}>{file}</span>
                  )}
                </button>
              )
            })
          )}
        </div>
        {/* Animated piece layer (transform-transitioned, 200ms cubic-bezier). */}
        <div className="pointer-events-none absolute inset-0 z-10">
          {pieceLayer.map((it) => {
            const isMoved = lastMove && lastMove.to === it.sq && lastMove.from !== lastMove.to
            const dx = isMoved ? (sqLeftPct(displayFiles.indexOf(lastMove!.from[0])) - sqLeftPct(it.fileIdx)) : 0
            const dy = isMoved ? (sqTopPct(displayRanks.indexOf(lastMove!.from[1])) - sqTopPct(it.rankIdx)) : 0
            return (
              <div
                key={it.sq}
                className="absolute"
                style={{
                  width: "12.5%",
                  height: "12.5%",
                  left: `${sqLeftPct(it.fileIdx)}%`,
                  top: `${sqTopPct(it.rankIdx)}%`,
                  transform: isMoved ? `translate(${dx}%, ${dy}%)` : undefined,
                  transition: isMoved ? "transform 200ms cubic-bezier(0.35, 0.7, 0.5, 1)" : undefined,
                  willChange: "transform",
                }}
              >
                <ChessPiece piece={it.piece} />
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
