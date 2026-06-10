import type { OrganismSubsystem, SubsystemCardDisplay } from "./organism-types"
import { getSubsystemTheme } from "./organism-theme"

type SubsystemCardProps = SubsystemCardDisplay & {
  activeId: OrganismSubsystem
  onActivate: (id: OrganismSubsystem) => void
}

export function SubsystemCard({
  id,
  activeId,
  onActivate,
  label,
  title,
  value,
  summary,
  detail,
  nextStep
}: SubsystemCardProps) {
  const isActive = activeId === id
  const theme = getSubsystemTheme(id)

  return (
    <button
      type="button"
      onClick={() => onActivate(id)}
      aria-pressed={isActive}
      aria-label={`Select ${label} subsystem`}
      style={{
        position: "relative",
        overflow: "hidden",
        width: "100%",
        textAlign: "left",
        padding: 22,
        borderRadius: 24,
        border: isActive ? `2px solid ${theme.accent}` : "1px solid rgba(255,255,255,0.08)",
        background: isActive ? theme.gradient : "rgba(255,255,255,0.02)",
        color: "white",
        cursor: "pointer",
        outline: "none",
        transform: isActive ? "translateY(-4px) scale(1.02)" : "translateY(0) scale(1)",
        boxShadow: isActive
          ? `0 26px 70px ${theme.glow}, 0 0 0 1px ${theme.accent}33 inset`
          : "0 10px 30px rgba(0,0,0,0.2)",
        transition: "all 250ms cubic-bezier(0.16, 1, 0.3, 1)",
        animation: isActive ? "cardFocus 2s infinite alternate" : "none"
      }}
      onFocus={(e) => {
        e.currentTarget.style.boxShadow = `0 0 0 4px ${theme.accent}44, ${isActive ? `0 26px 70px ${theme.glow}` : "0 10px 30px rgba(0,0,0,0.2)"}`
      }}
      onBlur={(e) => {
        e.currentTarget.style.boxShadow = isActive
          ? `0 26px 70px ${theme.glow}, 0 0 0 1px ${theme.accent}33 inset`
          : "0 10px 30px rgba(0,0,0,0.2)"
      }}
    >
      <div
        style={{
          position: "absolute",
          inset: 0,
          background: "linear-gradient(115deg, transparent 20%, rgba(255,255,255,0.14) 50%, transparent 80%)",
          transform: "translateX(-120%)",
          animation: isActive ? "scanSweep 2.8s linear infinite" : "none",
          pointerEvents: "none"
        }}
      />

      <div
        style={{
          position: "absolute",
          top: -30,
          right: -10,
          width: 150,
          height: 150,
          borderRadius: "50%",
          background: "rgba(255,255,255,0.10)",
          filter: "blur(10px)"
        }}
      />

      <div style={{ position: "relative", zIndex: 1 }}>
        <div
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 10,
            padding: "7px 12px",
            borderRadius: 999,
            background: "rgba(255,255,255,0.10)",
            marginBottom: 14,
            fontSize: 12,
            fontWeight: 800,
            letterSpacing: "0.08em",
            textTransform: "uppercase"
          }}
        >
          <span
            style={{
              width: 10,
              height: 10,
              borderRadius: "50%",
              background: theme.accent,
              boxShadow: `0 0 18px ${theme.accent}`,
              animation: "statusBlink 1.8s ease-in-out infinite"
            }}
          />
          {label}
        </div>

        <div style={{ fontSize: 14, opacity: 0.78, marginBottom: 8 }}>{title}</div>
        <div style={{ fontSize: 32, fontWeight: 900, lineHeight: 1.02, marginBottom: 10 }}>{value}</div>
        <div style={{ lineHeight: 1.65, color: "rgba(255,255,255,0.92)", marginBottom: 14 }}>{summary}</div>

        <div
          style={{
            borderRadius: 16,
            padding: 14,
            background: isActive ? "rgba(255,255,255,0.16)" : "rgba(255,255,255,0.08)",
            border: "1px solid rgba(255,255,255,0.10)",
            marginBottom: 12
          }}
        >
          <div style={{ fontSize: 12, letterSpacing: "0.06em", textTransform: "uppercase", opacity: 0.76, marginBottom: 6 }}>
            Operator meaning
          </div>
          <div style={{ lineHeight: 1.6 }}>{detail}</div>
        </div>

        <div
          style={{
            fontSize: 13,
            color: "rgba(255,255,255,0.84)",
            paddingTop: 4
          }}
        >
          Next move: {nextStep}
        </div>
      </div>
    </button>
  )
}
