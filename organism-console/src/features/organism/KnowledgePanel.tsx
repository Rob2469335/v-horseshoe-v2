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
        border: `1px solid ${accent}26`,
        boxShadow: "0 18px 40px rgba(0,0,0,0.22)",
        
      }}
    ><div style={{ position: "absolute", inset: 0, background: `radial-gradient(circle at 85% 10%, ${accent}10, transparent 26%)`, pointerEvents: "none" }} />

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
            fontSize: 11,
            fontWeight: 800,
            textTransform: "uppercase",
            letterSpacing: "0.06em",
            marginBottom: 14
          }}
        >
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: "50%",
              background: accent,
              
            }}
          />
          {badge}
        </div>

        <h2 style={{ margin: "0 0 10px", color: "white", fontSize: 22, lineHeight: 1.1 }}>{title}</h2>
        <p style={{ margin: "0 0 16px", color: "rgba(255,255,255,0.74)", lineHeight: 1.68, fontSize: 14 }}>{intro}</p>

        <div style={{ display: "grid", gap: 10 }}>
          {bullets.map((bullet) => (
            <div
              key={bullet}
              style={{
                borderRadius: 14,
                padding: "12px 14px",
                background: "rgba(255,255,255,0.04)",
                border: "1px solid rgba(255,255,255,0.07)",
                color: "rgba(255,255,255,0.88)", lineHeight: 1.58, fontSize: 14
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

