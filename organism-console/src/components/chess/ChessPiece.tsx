/**
 * Real chess piece rendering — the cburnett piece set (the professional
 * open standard, BSD-licensed from Wikimedia Commons) served as SVG assets
 * and drawn as CSS background-images scaled to fill the square, exactly like
 * lichess/chessground. These are the actual community-polished vector pieces,
 * not hand-drawn approximations.
 *
 * Assets: organism-console/public/pieces/cburnett/{w,b}{K,Q,R,B,N,P}.svg
 * Source: https://commons.wikimedia.org/wiki/Category:SVG_chess_pieces (cburnett, 2006)
 * License: BSD (2-clause) / CC-BY-SA 3.0 / GFDL / GPL — using the BSD option.
 */

export function pieceUrl(piece: string): string {
  const color = piece === piece.toUpperCase() ? "w" : "b"
  const type = piece.toUpperCase()
  return `/pieces/cburnett/${color}${type}.svg`
}

export function ChessPiece({ piece }: { piece: string }) {
  return (
    <div
      className="h-full w-full"
      style={{
        backgroundImage: `url("${pieceUrl(piece)}")`,
        backgroundSize: "100% 100%",
        backgroundRepeat: "no-repeat",
        backgroundPosition: "center",
      }}
    />
  )
}
