import type { KnowledgePanelDisplay } from "./organism-types"

type KnowledgePanelProps = KnowledgePanelDisplay

export function KnowledgePanel({ badge, title, intro, bullets, accent }: KnowledgePanelProps) {
  return (
    <article
      style={{
        position: "relative",
        overflow: "hidden",
        borderRadius: 22,
        padding: 22,
        background: "linear-gradient(180deg, rgba(14,20,35,0.96), rgba(8,11,20,0.98))",
        border: `1px solid ${accent}33`,
        boxShadow: "0 20px 50px rgba(0,0,0,0.24)",
        animation: "floatPanel 8.5s ease-in-out infinite"
      }}
    >
      <div
        style={{
          position: "absolute",
          inset: 0,
          background: "linear-gradient(120deg, transparent 0%, rgba(255,255,255,0.05) 48%, transparent 100%)",
          transform: "translateX(-120%)",
          animation: "scanSweep 6.2s linear infinite"
        }}
      />

      <div style={{ position: "relative", zIndex: 1 }}>
        <div
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 10,
            padding: "6px 12px",
            borderRadius: 999,
            background: `${accent}1A`,
            color: accent,
            fontSize: 12,
            fontWeight: 800,
            textTransform: "uppercase",
            letterSpacing: "0.06em",
            marginBottom: 14
          }}
        >
          <span
            style={{
              width: 10,
              height: 10,
              borderRadius: "50%",
              background: accent,
              boxShadow: `0 0 16px ${accent}`,
              animation: "statusBlink 2s ease-in-out infinite"
            }}
          />
          {badge}
        </div>

        <h2 style={{ margin: "0 0 10px", color: "white", fontSize: 24 }}>{title}</h2>
        <p style={{ margin: "0 0 16px", color: "rgba(255,255,255,0.74)", lineHeight: 1.7 }}>{intro}</p>

        <div style={{ display: "grid", gap: 10 }}>
          {bullets.map((bullet) => (
            <div
              key={bullet}
              style={{
                borderRadius: 14,
                padding: "12px 14px",
                background: "rgba(255,255,255,0.04)",
                border: "1px solid rgba(255,255,255,0.08)",
                color: "rgba(255,255,255,0.90)",
                lineHeight: 1.6
              }}
            >
              {bullet}
            </div>
          ))}
        </div>
      </div>
    </article>
  )
}
