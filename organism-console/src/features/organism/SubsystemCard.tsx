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
        padding: 20,
        borderRadius: 22,
        border: isActive ? `1px solid ${theme.accent}55` : "1px solid rgba(255,255,255,0.08)",
        background: isActive ? `linear-gradient(180deg, ${theme.tint}, rgba(255,255,255,0.04))` : "linear-gradient(180deg, rgba(255,255,255,0.04), rgba(255,255,255,0.025))",
        color: "white",
        cursor: "pointer",
        outline: "none",
        transform: isActive ? "translateY(-2px)" : "translateY(0)",
        boxShadow: isActive ? `0 18px 42px rgba(0,0,0,0.28), 0 0 0 1px ${theme.accent}18 inset` : "0 10px 24px rgba(0,0,0,0.16)",
        transition: "transform 180ms cubic-bezier(0.16, 1, 0.3, 1), box-shadow 220ms cubic-bezier(0.16, 1, 0.3, 1), border-color 220ms ease, background 220ms ease",
        animation: "none"
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
          top: -18,
          right: -6,
          width: 110,
          height: 110,
          borderRadius: "50%",
          background: `${theme.accent}14`,
          filter: "blur(14px)"
        }}
      />

      <div style={{ position: "relative", zIndex: 1 }}>
        <div
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 10,
            padding: "6px 11px",
            borderRadius: 999,
            background: `${theme.accent}14`,
            marginBottom: 14,
            fontSize: 11,
            fontWeight: 800,
            letterSpacing: "0.08em",
            textTransform: "uppercase"
          }}
        >
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: "50%",
              background: theme.accent,
              boxShadow: isActive ? `0 0 12px ${theme.accent}` : "none"
            }}
          />
          {label}
        </div>

        <div style={{ fontSize: 13, color: "rgba(255,255,255,0.62)", marginBottom: 8 }}>{title}</div>
        <div style={{ fontSize: 30, fontWeight: 900, lineHeight: 1.02, marginBottom: 10, fontVariantNumeric: "tabular-nums" }}>{value}</div>
        <div style={{ lineHeight: 1.62, color: "rgba(255,255,255,0.88)", marginBottom: 14, fontSize: 14 }}>{summary}</div>

        <div
          style={{
            borderRadius: 16,
            padding: 14,
            background: "rgba(255,255,255,0.05)",
            border: "1px solid rgba(255,255,255,0.08)",
            marginBottom: 12
          }}
        >
          <div style={{ fontSize: 11, letterSpacing: "0.06em", textTransform: "uppercase", opacity: 0.76, marginBottom: 6 }}>
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




