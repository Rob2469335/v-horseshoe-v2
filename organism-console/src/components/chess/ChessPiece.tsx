/**
 * Custom SVG chess piece set — self-authored, license-clean (no new dependency,
 * no GPL asset import). Each piece is a 45x45 SVG drawn with layered gradients
 * for a modern Staunton-inspired look: ivory/cream fill for White, charcoal
 * fill with a light rim for Black, soft drop shadows for depth on the board.
 *
 * The standard 45x45 viewBox means the set scales to any board size and could
 * be swapped for another set later without touching the board component.
 */
import React from "react"

export type PieceChar = "K" | "Q" | "R" | "B" | "N" | "P" | "k" | "q" | "r" | "b" | "n" | "p"

function PieceSvg({ children, dark }: { children: React.ReactNode; dark: boolean }) {
  return (
    <svg
      viewBox="0 0 45 45"
      width="100%"
      height="100%"
      style={{ display: "block" }}
      aria-hidden="true"
    >
      <defs>
        <linearGradient id={dark ? "pBlackBody" : "pWhiteBody"} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={dark ? "#3a3f4b" : "#fafaf6"} />
          <stop offset="55%" stopColor={dark ? "#23262e" : "#e8e4d8"} />
          <stop offset="100%" stopColor={dark ? "#14161b" : "#c9c2ae"} />
        </linearGradient>
        <linearGradient id={dark ? "pBlackShine" : "pWhiteShine"} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={dark ? "#6b7280" : "#ffffff"} />
          <stop offset="100%" stopColor={dark ? "#3a3f4b" : "#e8e4d8"} />
        </linearGradient>
        <radialGradient id="pShadowG" cx="50%" cy="42%" r="65%">
          <stop offset="0%" stopColor="rgba(0,0,0,0.28)" />
          <stop offset="70%" stopColor="rgba(0,0,0,0.08)" />
          <stop offset="100%" stopColor="rgba(0,0,0,0)" />
        </radialGradient>
      </defs>
      <ellipse cx="22.5" cy="41" rx="17" ry="2.6" fill="url(#pShadowG)" />
      <g fill={dark ? "url(#pBlackBody)" : "url(#pWhiteBody)"} stroke={dark ? "#000" : "#7a7466"} strokeWidth="0.8">
        {children}
      </g>
    </svg>
  )
}

function Pawn({ dark }: { dark: boolean }) {
  return (
    <PieceSvg dark={dark}>
      <path d="M22.5 3.5 a2.6 2.6 0 1 1 0 5.2 a2.6 2.6 0 1 1 0 -5.2 Z" fill="url(#pShadowG)" stroke="none" />
      <circle cx="22.5" cy="6.2" r="2.6" fill={dark ? "url(#pBlackShine)" : "url(#pWhiteShine)"} stroke="none" />
      <path d="M20 8.8 c-1.6 1.2 -3 2.6 -3.6 4.6 c-1.4 4.6 0.4 8.6 -2 12.4 c-1.6 2.4 -2.6 5 -2.6 7.6 h20 c0 -2.6 -1 -5.2 -2.6 -7.6 c-2.4 -3.8 -0.6 -7.8 -2 -12.4 c-0.6 -2 -2 -3.4 -3.6 -4.6 Z" />
      <path d="M20 8.8 c1 0.8 1.6 1.7 2 2.8 c0.8 -1 1.9 -1.8 3.2 -2.2 c-0.7 1.3 -1.2 2.7 -1.5 4.2 l-1.7 2.4 l-1.7 -2.4 c-0.3 -1.5 -0.8 -2.9 -1.5 -4.2 c1.3 0.4 2.4 1.2 3.2 2.2 Z" fill={dark ? "url(#pBlackShine)" : "url(#pWhiteShine)"} stroke="none" />
      <ellipse cx="22.5" cy="40.2" rx="8.5" ry="1.8" />
    </PieceSvg>
  )
}

function Rook({ dark }: { dark: boolean }) {
  return (
    <PieceSvg dark={dark}>
      <path d="M22.5 4 a1.8 1.8 0 1 1 0 3.6 a1.8 1.8 0 1 1 0 -3.6 Z" fill="url(#pShadowG)" stroke="none" />
      <path d="M11 4.5 h3 l1 5 h2 l1 -5 h9 l1 5 h2 l1 -5 h3 l-1 8 h-4 v3.5 h-14 V12.5 h-4 Z" />
      <path d="M12 12.5 h6 v3.5 h-6 Z M27 12.5 h6 v3.5 h-6 Z" fill={dark ? "url(#pBlackShine)" : "url(#pWhiteShine)"} stroke="none" />
      <path d="M13 16.5 h19 c0.4 1.4 0.2 2.8 -0.4 4 c-1.4 2.6 -4.4 3.2 -6 4.6 c-1.4 1.2 -2.2 2.6 -2.6 4.4 h-0.5 c-0.4 -1.8 -1.2 -3.2 -2.6 -4.4 c-1.6 -1.4 -4.6 -2 -6 -4.6 c-0.6 -1.2 -0.8 -2.6 -0.4 -4 Z" />
      <ellipse cx="22.5" cy="40.2" rx="9" ry="1.9" />
    </PieceSvg>
  )
}

function Knight({ dark }: { dark: boolean }) {
  return (
    <PieceSvg dark={dark}>
      <path d="M23 6 a2.4 2.4 0 1 1 0 4.8 a2.4 2.4 0 1 1 0 -4.8 Z" fill="url(#pShadowG)" stroke="none" />
      <path d="M22.5 7.6 c-1.2 -0.2 -2.4 0.2 -3.2 1 c-1.4 1.4 -1.8 3.4 -1.2 5.2 c-2 -1 -4.4 -0.6 -5.9 0.9 c-0.4 0.4 -0.6 0.9 -0.8 1.4 l-0.2 0.5 l0.3 0.5 c0.2 0.4 0.5 0.7 0.8 1 l1.4 1.2 c-0.4 0.8 -0.6 1.7 -0.5 2.6 l0.1 1 l0.8 -0.4 c1.9 -1 4 -1.5 6.1 -1.5 c0.6 0 1.2 0 1.8 0.1 l-0.4 0.9 c-0.4 1 -0.3 2.1 0.2 3 l0.3 0.5 l-0.5 0.3 c-3.6 2.1 -6.1 5.2 -7 8.9 l-0.2 0.8 h12 c0.4 -1.2 0.6 -2.5 0.5 -3.8 c-0.2 -2.4 -1.1 -4.7 -2.6 -6.5 c-1.6 -1.9 -3.7 -3.3 -6 -4 c-1.1 -0.3 -2.1 -0.8 -3 -1.4 c1.8 -0.4 3.4 -1.3 4.6 -2.6 c1.2 -1.3 1.9 -3 2 -4.8 c-0.9 0.4 -1.9 0.6 -2.8 0.5 Z" />
      <path d="M22.5 7.6 c1.2 -0.2 2.4 0.2 3.2 1 c1.4 1.4 1.8 3.4 1.2 5.2 c2 -1 4.4 -0.6 5.9 0.9 c0.4 0.4 0.6 0.9 0.8 1.4 l0.2 0.5 l-0.3 0.5 c-0.2 0.4 -0.5 0.7 -0.8 1 l-1.4 1.2 c0.4 0.8 0.6 1.7 0.5 2.6 l-0.1 1 l-0.8 -0.4 c-1.9 -1 -4 -1.5 -6.1 -1.5 c-0.6 0 -1.2 0 -1.8 0.1 l0.4 0.9 c0.4 1 0.3 2.1 -0.2 3 l-0.3 0.5 l0.5 0.3 c3.6 2.1 6.1 5.2 7 8.9 l0.2 0.8 h-12 c-0.4 -1.2 -0.6 -2.5 -0.5 -3.8 c0.2 -2.4 1.1 -4.7 2.6 -6.5 c1.6 -1.9 3.7 -3.3 6 -4 c1.1 -0.3 2.1 -0.8 3 -1.4 c-1.8 -0.4 -3.4 -1.3 -4.6 -2.6 c-1.2 -1.3 -1.9 -3 -2 -4.8 c0.9 0.4 1.9 0.6 2.8 0.5 Z" fill={dark ? "url(#pBlackShine)" : "url(#pWhiteShine)"} stroke="none" opacity="0.45" />
      <ellipse cx="22.5" cy="40.2" rx="9" ry="1.9" />
    </PieceSvg>
  )
}

function Bishop({ dark }: { dark: boolean }) {
  return (
    <PieceSvg dark={dark}>
      <circle cx="22.5" cy="5" r="2.1" fill={dark ? "url(#pBlackShine)" : "url(#pWhiteShine)"} stroke="none" />
      <path d="M22.5 7 c-0.8 0 -1.6 0.2 -2.2 0.6 c-1.6 -0.6 -3.4 -0.2 -4.6 0.9 c-1.6 1.4 -2 3.6 -1.1 5.4 c-2 0.6 -3.5 2.2 -4 4.2 c-0.3 1.3 -0.1 2.6 0.6 3.8 c-0.6 0.6 -0.9 1.4 -0.9 2.2 c0 2 1.4 3.7 3.3 4.2 c0.4 4.4 2.4 8 5.9 10 l0.6 0.4 l0.6 -0.4 c3.5 -2 5.5 -5.6 5.9 -10 c1.9 -0.5 3.3 -2.2 3.3 -4.2 c0 -0.8 -0.3 -1.6 -0.9 -2.2 c0.7 -1.2 0.9 -2.5 0.6 -3.8 c-0.5 -2 -2 -3.6 -4 -4.2 c0.9 -1.8 0.5 -4 -1.1 -5.4 c-1.2 -1.1 -3 -1.5 -4.6 -0.9 c-0.6 -0.4 -1.4 -0.6 -2.2 -0.6 Z" />
      <path d="M22.5 7 c0.8 0 1.6 0.2 2.2 0.6 c1.6 -0.6 3.4 -0.2 4.6 0.9 c1.6 1.4 2 3.6 1.1 5.4 c2 0.6 3.5 2.2 4 4.2 c0.3 1.3 0.1 2.6 -0.6 3.8 c0.6 0.6 0.9 1.4 0.9 2.2 c0 2 -1.4 3.7 -3.3 4.2 c-0.4 4.4 -2.4 8 -5.9 10 c-3.5 -2 -5.5 -5.6 -5.9 -10 c-1.9 -0.5 -3.3 -2.2 -3.3 -4.2 c0 -0.8 0.3 -1.6 0.9 -2.2 c-0.7 -1.2 -0.9 -2.5 -0.6 -3.8 c0.5 -2 2 -3.6 4 -4.2 c-0.9 -1.8 -0.5 -4 1.1 -5.4 c1.2 -1.1 3 -1.5 4.6 -0.9 c0.6 -0.4 1.4 -0.6 2.2 -0.6 Z" fill={dark ? "url(#pBlackShine)" : "url(#pWhiteShine)"} stroke="none" opacity="0.4" />
      <path d="M20.5 17.5 c-0.5 0 -0.9 0.4 -0.9 0.9 c0 0.8 0.3 1.5 0.9 2.1 c0.6 -0.6 0.9 -1.3 0.9 -2.1 c0 -0.5 -0.4 -0.9 -0.9 -0.9 Z" fill={dark ? "#000" : "#7a7466"} stroke="none" />
      <path d="M24.5 17.5 c-0.5 0 -0.9 0.4 -0.9 0.9 c0 0.8 0.3 1.5 0.9 2.1 c0.6 -0.6 0.9 -1.3 0.9 -2.1 c0 -0.5 -0.4 -0.9 -0.9 -0.9 Z" fill={dark ? "#000" : "#7a7466"} stroke="none" />
      <ellipse cx="22.5" cy="40.2" rx="9" ry="1.9" />
    </PieceSvg>
  )
}

function Queen({ dark }: { dark: boolean }) {
  return (
    <PieceSvg dark={dark}>
      {[7, 14, 21, 28, 35].map((x, i) => (
        <circle key={i} cx={x} cy="5.5" r="1.8" fill={dark ? "url(#pBlackShine)" : "url(#pWhiteShine)"} stroke="none" />
      ))}
      <path d="M7 8 c0.6 -1.4 1.6 -2.5 3 -3 l-1 -3.5 l-3.5 2 l1 3 c0.4 0.5 0.7 1.1 0.8 1.7 Z M38 8 c-0.6 -1.4 -1.6 -2.5 -3 -3 l1 -3.5 l3.5 2 l-1 3 c-0.4 0.5 -0.7 1.1 -0.8 1.7 Z" fill={dark ? "url(#pBlackShine)" : "url(#pWhiteShine)"} stroke="none" />
      <path d="M14 8 c0.4 -1 1.1 -1.9 2 -2.5 l-0.4 -3.5 l-3.6 1.5 l0.8 2.9 c0.5 0.5 0.9 1.1 1.2 1.6 Z M31 8 c-0.4 -1 -1.1 -1.9 -2 -2.5 l0.4 -3.5 l3.6 1.5 l-0.8 2.9 c-0.5 0.5 -0.9 1.1 -1.2 1.6 Z" fill={dark ? "url(#pBlackShine)" : "url(#pWhiteShine)"} stroke="none" />
      <path d="M21 8 c0.2 -1.1 0.8 -2.1 1.7 -2.9 l-0.2 -3.6 l-3.4 1.4 l0.6 3 c0.5 0.6 0.9 1.3 1.3 2.1 Z M24 8 c-0.2 -1.1 -0.8 -2.1 -1.7 -2.9 l0.2 -3.6 l3.4 1.4 l-0.6 3 c-0.5 0.6 -0.9 1.3 -1.3 2.1 Z" fill={dark ? "url(#pBlackShine)" : "url(#pWhiteShine)"} stroke="none" />
      <path d="M11 8 c1.4 -2.2 3.8 -3.6 6.4 -3.8 c1.6 -0.2 3.3 0.2 4.6 1.1 c1.3 -0.9 3 -1.3 4.6 -1.1 c2.6 0.2 5 1.6 6.4 3.8 c-0.4 0.2 -0.8 0.3 -1.2 0.3 c-0.6 0 -1.2 -0.2 -1.7 -0.5 l-0.3 -0.2 l-0.1 0.4 c-0.6 2.2 -1.5 4.2 -2.6 6.2 c-2.6 -1.2 -5.6 -1.2 -8.2 0 c-1.1 -2 -2 -4 -2.6 -6.2 l-0.1 -0.4 l-0.3 0.2 c-0.5 0.3 -1.1 0.5 -1.7 0.5 c-0.4 0 -0.8 -0.1 -1.2 -0.3 Z" />
      <path d="M14 16 c1.2 1.2 2 2.7 2.4 4.4 c1.9 -0.8 4.1 -0.8 6 0 c0.4 -1.7 1.2 -3.2 2.4 -4.4 c-1.7 -1.8 -4.1 -2.8 -6.4 -2.8 c-2.3 0 -4.7 1 -6.4 2.8 Z" fill={dark ? "url(#pBlackShine)" : "url(#pWhiteShine)"} stroke="none" />
      <path d="M9 19.5 h27 c0.4 2 0 4 -1 5.7 c-1.6 2.7 -4.4 4.2 -7.3 4.2 c-1.6 0 -3.1 -0.4 -4.4 -1.2 l-0.8 -0.5 l-0.8 0.5 c-1.3 0.8 -2.8 1.2 -4.4 1.2 c-2.9 0 -5.7 -1.5 -7.3 -4.2 c-1 -1.7 -1.4 -3.7 -1 -5.7 Z" />
      <path d="M9 19.5 c1.4 -0.8 3 -1.2 4.7 -1.2 c2.2 0 4.4 0.6 6.3 1.7 c-0.6 1.6 -1.6 3 -3 4.1 c-1.6 1.2 -3.5 1.8 -5.4 1.8 c-1.6 0 -3.1 -0.5 -4.3 -1.4 c-0.8 -0.9 -1.3 -2 -1.4 -3.2 c0.3 -0.6 0.9 -1.2 1.6 -1.4 Z M36 19.5 c-1.4 -0.8 -3 -1.2 -4.7 -1.2 c-2.2 0 -4.4 0.6 -6.3 1.7 c0.6 1.6 1.6 3 3 4.1 c1.6 1.2 3.5 1.8 5.4 1.8 c1.6 0 3.1 -0.5 4.3 -1.4 c0.8 -0.9 1.3 -2 1.4 -3.2 c-0.3 -0.6 -0.9 -1.2 -1.6 -1.4 Z" fill={dark ? "url(#pBlackShine)" : "url(#pWhiteShine)"} stroke="none" opacity="0.4" />
      <ellipse cx="22.5" cy="40.2" rx="9.5" ry="1.9" />
    </PieceSvg>
  )
}

function King({ dark }: { dark: boolean }) {
  return (
    <PieceSvg dark={dark}>
      <path d="M22.5 2.5 h3.5 v3 h-3.5 Z M21 5 h3.5 v3 h-3.5 Z" fill={dark ? "url(#pBlackShine)" : "url(#pWhiteShine)"} stroke="none" />
      <path d="M22.5 5.5 l-1.8 0.8 l0.8 1.8 c0.6 -0.6 1.4 -1 2.3 -1 c0.9 0 1.7 0.4 2.3 1 l0.8 -1.8 Z" fill={dark ? "url(#pBlackShine)" : "url(#pWhiteShine)"} stroke="none" />
      <circle cx="22.5" cy="11.5" r="2.6" fill={dark ? "url(#pBlackShine)" : "url(#pWhiteShine)"} stroke="none" />
      <path d="M18.5 14 c-1.6 1.2 -2.8 2.8 -3.4 4.7 c-0.8 2.4 -0.8 5 0 7.4 c-1.6 1.8 -2.5 4.1 -2.5 6.5 h20 c0 -2.4 -0.9 -4.7 -2.5 -6.5 c0.8 -2.4 0.8 -5 0 -7.4 c-0.6 -1.9 -1.8 -3.5 -3.4 -4.7 c-2.6 -1.9 -6 -1.9 -8.2 0 Z" />
      <path d="M18.5 14 c1.2 -0.9 2.6 -1.4 4 -1.4 c1.4 0 2.8 0.5 4 1.4 c-1 1.4 -2.5 2.4 -4.2 2.6 l0.2 2 l-1.6 1.6 l-1.6 -1.6 l0.2 -2 c-1.7 -0.2 -3.2 -1.2 -4.2 -2.6 Z" fill={dark ? "url(#pBlackShine)" : "url(#pWhiteShine)"} stroke="none" />
      <path d="M22.5 14.6 c-0.9 0 -1.7 0.3 -2.4 0.8 c0.4 1.3 1.4 2.4 2.6 3 c-1.2 0.6 -2.2 1.7 -2.6 3 c0.7 0.5 1.5 0.8 2.4 0.8 c0.9 0 1.7 -0.3 2.4 -0.8 c-0.4 -1.3 -1.4 -2.4 -2.6 -3 c1.2 -0.6 2.2 -1.7 2.6 -3 c-0.7 -0.5 -1.5 -0.8 -2.4 -0.8 Z" fill={dark ? "#000" : "#fff"} stroke="none" opacity="0.28" />
      <ellipse cx="22.5" cy="40.2" rx="9.5" ry="1.9" />
    </PieceSvg>
  )
}

export function ChessPiece({ piece, size }: { piece: string; size?: number }) {
  const dark = piece === piece.toLowerCase()
  const render = () => {
    switch (piece.toLowerCase()) {
      case "p": return <Pawn dark={dark} />
      case "r": return <Rook dark={dark} />
      case "n": return <Knight dark={dark} />
      case "b": return <Bishop dark={dark} />
      case "q": return <Queen dark={dark} />
      case "k": return <King dark={dark} />
      default: return null
    }
  }
  return (
    <div className="flex h-full w-full items-center justify-center" style={{ filter: dark ? "drop-shadow(0 1px 1px rgba(0,0,0,0.35))" : "drop-shadow(0 1px 1px rgba(0,0,0,0.2))" }}>
      <div style={{ width: size ?? "88%", height: size ?? "88%" }}>{render()}</div>
    </div>
  )
}
