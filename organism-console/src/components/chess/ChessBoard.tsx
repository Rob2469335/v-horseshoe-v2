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
import { useMemo, useEffect, useRef, useState } from "react"
import { ChessPiece } from "./ChessPiece"

function AnimatedPiece({
  piece,
  fileIdx,
  rankIdx,
  isMoved,
  lastMove,
  displayFiles,
  displayRanks,
  sqLeftPct,
  sqTopPct
}: {
  piece: string
  fileIdx: number
  rankIdx: number
  isMoved: boolean
  lastMove: { from: string; to: string } | null
  displayFiles: string[]
  displayRanks: string[]
  sqLeftPct: (i: number) => number
  sqTopPct: (i: number) => number
}) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (isMoved && lastMove) {
      const fromFileIdx = displayFiles.indexOf(lastMove.from[0])
      const fromRankIdx = displayRanks.indexOf(lastMove.from[1])
      const dx = (fromFileIdx - fileIdx) * 100
      const dy = (fromRankIdx - rankIdx) * 100
      if (dx !== 0 || dy !== 0) {
        ref.current?.animate(
          [
            { transform: `translate(${dx}%, ${dy}%)` },
            { transform: 'translate(0px, 0px)' }
          ],
          {
            duration: 200,
            easing: 'cubic-bezier(0.35, 0.7, 0.5, 1)'
          }
        )
      }
    }
  }, [isMoved, lastMove, fileIdx, rankIdx, displayFiles, displayRanks])

  return (
    <div
      ref={ref}
      className="absolute"
      style={{
        width: "12.5%",
        height: "12.5%",
        left: `${sqLeftPct(fileIdx)}%`,
        top: `${sqTopPct(rankIdx)}%`,
        willChange: "transform",
      }}
    >
      <ChessPiece piece={piece} />
    </div>
  )
}

const FILES = "abcdefgh"
const RANKS = "87654321"

// Vibrant board themes — rich, saturated two-tone palettes (2026 look: vivid
// wood/emerald/ocean/royal/violet). The lichess flat recipe, but colorful.
export type BoardThemeKey = "vibrant" | "emerald" | "ocean" | "royal" | "violet" | "rose" | "amber"

export const BOARD_THEMES: Record<BoardThemeKey, { light: string; dark: string; name: string }> = {
  vibrant: { light: "#f7e8a0", dark: "#3f8f3f", name: "Vibrant" },
  emerald: { light: "#d7f7b8", dark: "#1f9e4f", name: "Emerald" },
  ocean: { light: "#b8ecf5", dark: "#0f7fb0", name: "Ocean" },
  royal: { light: "#c9d8ff", dark: "#2f52c9", name: "Royal" },
  violet: { light: "#e3c9ff", dark: "#7a3fd0", name: "Violet" },
  rose: { light: "#ffd3de", dark: "#d23f6a", name: "Rose" },
  amber: { light: "#ffe9b3", dark: "#e08a1e", name: "Amber" },
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
  onProposeMove?: (uci: string) => void
  orientation?: "white" | "black"
  highlights?: BoardHighlights
  evalBar?: { whitePct: number } | null
  theme?: BoardThemeKey | string
}

export default function ChessBoard({
  fen,
  interactive = false,
  onSquareClick,
  onProposeMove,
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

  // Right-click-drag move proposal (lichess-style arrow): the user draws an
  // arrow from a square to a target; on release the UCI is sent to the coach
  // ("what if I play this?"). The preview arrow renders while dragging.
  const boardRef = useRef<HTMLDivElement | null>(null)
  const [proposalDrag, setProposalDrag] = useState<{ from: string; to: string } | null>(null)

  const sqFromPoint = (e: React.MouseEvent | React.PointerEvent) => {
    const el = boardRef.current
    if (!el) return null
    const rect = el.getBoundingClientRect()
    const x = (e.clientX - rect.left) / rect.width * 8
    const y = (e.clientY - rect.top) / rect.height * 8
    const fIdx = Math.min(7, Math.max(0, Math.floor(x)))
    const rIdx = Math.min(7, Math.max(0, Math.floor(y)))
    return displayFiles[fIdx] + displayRanks[rIdx]
  }

  const onContextMenu = (e: React.MouseEvent) => {
    e.preventDefault()
    if (!interactive || !onProposeMove) return
    const sq = sqFromPoint(e)
    setProposalDrag(sq ? { from: sq, to: sq } : null)
  }
  const onContextMove = (e: React.MouseEvent) => {
    if (!proposalDrag) return
    e.preventDefault()
    const sq = sqFromPoint(e)
    if (sq) setProposalDrag((d) => (d && d.to !== sq ? { ...d, to: sq } : d))
  }
  const onContextUp = (e: React.MouseEvent) => {
    if (!proposalDrag) return
    e.preventDefault()
    const to = sqFromPoint(e) ?? proposalDrag.to
    setProposalDrag(null)
    if (onProposeMove && proposalDrag.from !== to) {
      onProposeMove(proposalDrag.from + to)
    }
  }

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
        ref={boardRef}
        className="relative w-full select-none overflow-hidden rounded-[4px] border border-black/50 shadow-[0_4px_12px_rgba(0,0,0,0.55)]"
        style={{ aspectRatio: "1 / 1", background: DARK, touchAction: "none" }}
        onContextMenu={onContextMenu}
        onMouseMove={onContextMove}
        onMouseUp={onContextUp}
        onMouseLeave={onContextUp}
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
              // Distinct gold coordinates: one consistent color that reads on
              // every theme, large and bold for fast scanning. Gold + dark
              // shadow stays legible on both light and dark squares.
              const coordColor = "#ffd54a"
              const coordShadow = "0 1px 2px rgba(0,0,0,0.95), 0 0 3px rgba(0,0,0,0.6)"
              const isLeftEdge = file === "a"
              const isRightEdge = file === "h"
              const isTopEdge = rank === "8"
              const isBottomEdge = rank === "1"
              const showCoord =
                (isLeftEdge && rank !== "8" && rank !== "1")
                || (isRightEdge && rank !== "8" && rank !== "1")
                || (isTopEdge && file !== "a" && file !== "h")
                || (isBottomEdge && file !== "a" && file !== "h")

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
                  {showCoord && (
                    <span
                      className="pointer-events-none absolute font-black leading-none"
                      style={{
                        color: coordColor,
                        textShadow: coordShadow,
                        fontSize: "clamp(17px, 3.4vw, 21px)",
                        ...(isTopEdge
                          ? { top: "2px", left: "4px" }
                          : isBottomEdge
                            ? { bottom: "2px", left: "4px" }
                            : isRightEdge
                              ? { bottom: "2px", right: "4px" }
                              : { top: "2px", left: "4px" }),
                      }}
                    >
                      {isTopEdge || isBottomEdge ? file : rank}
                    </span>
                  )}
                </button>
              )
            })
          )}
        </div>
        <div className="pointer-events-none absolute inset-0 z-10">
          {pieceLayer.map((it) => {
            const isMoved = !!lastMove && lastMove.to === it.sq && lastMove.from !== lastMove.to
            return (
              <AnimatedPiece
                key={it.sq}
                piece={it.piece}
                fileIdx={it.fileIdx}
                rankIdx={it.rankIdx}
                isMoved={isMoved}
                lastMove={lastMove || null}
                displayFiles={displayFiles}
                displayRanks={displayRanks}
                sqLeftPct={sqLeftPct}
                sqTopPct={sqTopPct}
              />
            )
          })}
        </div>
        {/* Best-move / hint arrows (SVG overlay above pieces). */}
        {(arrows.length > 0 || proposalDrag) && (
          <svg viewBox="0 0 100 100" className="pointer-events-none absolute inset-0 z-20 h-full w-full">
            {arrows.map((a, i) => {
              const fromF = displayFiles.indexOf(a.from[0])
              const fromR = displayRanks.indexOf(a.from[1])
              const toF = displayFiles.indexOf(a.to[0])
              const toR = displayRanks.indexOf(a.to[1])
              if (fromF < 0 || toF < 0 || fromR < 0 || toR < 0) return null
              const x1 = fromF * 12.5 + 6.25
              const y1 = fromR * 12.5 + 6.25
              const x2 = toF * 12.5 + 6.25
              const y2 = toR * 12.5 + 6.25
              const dx = x2 - x1
              const dy = y2 - y1
              const len = Math.hypot(dx, dy) || 1
              const ux = dx / len
              const uy = dy / len
              const sx = x1 + ux * 8
              const sy = y1 + uy * 8
              const ex = x2 - ux * 7
              const ey = y2 - uy * 7
              const color = a.color ?? "rgba(20,170,110,0.9)"
              const head = 4.5
              return (
                <g key={i}>
                  <path d={`M${sx} ${sy} L${ex} ${ey}`} stroke={color} strokeWidth="2.6" strokeLinecap="round" fill="none" />
                  <path
                    d={`M${ex} ${ey} L${ex - ux * head - uy * head * 0.55} ${ey - uy * head + ux * head * 0.55} L${ex - ux * head + uy * head * 0.55} ${ey - uy * head - ux * head * 0.55} Z`}
                    fill={color}
                  />
                </g>
              )
            })}
            {proposalDrag && proposalDrag.from !== proposalDrag.to && (() => {
              const fromF = displayFiles.indexOf(proposalDrag.from[0])
              const fromR = displayRanks.indexOf(proposalDrag.from[1])
              const toF = displayFiles.indexOf(proposalDrag.to[0])
              const toR = displayRanks.indexOf(proposalDrag.to[1])
              if (fromF < 0 || toF < 0 || fromR < 0 || toR < 0) return null
              const x1 = fromF * 12.5 + 6.25
              const y1 = fromR * 12.5 + 6.25
              const x2 = toF * 12.5 + 6.25
              const y2 = toR * 12.5 + 6.25
              const dx = x2 - x1
              const dy = y2 - y1
              const len = Math.hypot(dx, dy) || 1
              const ux = dx / len
              const uy = dy / len
              const sx = x1 + ux * 8
              const sy = y1 + uy * 8
              const ex = x2 - ux * 7
              const ey = y2 - uy * 7
              const head = 4.5
              const color = "rgba(240,200,80,0.85)"
              return (
                <g>
                  <path d={`M${sx} ${sy} L${ex} ${ey}`} stroke={color} strokeWidth="2.6" strokeLinecap="round" fill="none" strokeDasharray="4 2" />
                  <path
                    d={`M${ex} ${ey} L${ex - ux * head - uy * head * 0.55} ${ey - uy * head + ux * head * 0.55} L${ex - ux * head + uy * head * 0.55} ${ey - uy * head - ux * head * 0.55} Z`}
                    fill={color}
                  />
                </g>
              )
            })()}
          </svg>
        )}
      </div>
    </div>
  )
}
