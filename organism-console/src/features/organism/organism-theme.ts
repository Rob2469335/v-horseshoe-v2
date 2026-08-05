export const organismTheme = {
  surface: {
    page: "#050815",
    panel: "rgba(10,14,28,0.96)",
    panelSoft: "rgba(255,255,255,0.04)",
    panelStrong: "rgba(18,30,60,0.94)",
    border: "rgba(255,255,255,0.10)",
    borderSoft: "rgba(255,255,255,0.08)",
    text: "#ecf3ff",
    textSoft: "rgba(236,243,255,0.78)",
    textMuted: "rgba(236,243,255,0.64)"
  },
  subsystem: {
    learning: {
      accent: "#00d1ff",
      glow: "rgba(0, 209, 255, 0.34)",
      tint: "rgba(0, 209, 255, 0.12)",
      gradient: "linear-gradient(180deg, rgba(0, 209, 255, 0.22), rgba(8, 15, 30, 0.92))"
    },
    healing: {
      accent: "#10b981",
      glow: "rgba(16, 185, 129, 0.32)",
      tint: "rgba(16, 185, 129, 0.12)",
      gradient: "linear-gradient(180deg, rgba(16, 185, 129, 0.20), rgba(7, 18, 16, 0.92))"
    },
    autonomy: {
      accent: "#d946ef",
      glow: "rgba(217, 70, 239, 0.32)",
      tint: "rgba(217, 70, 239, 0.12)",
      gradient: "linear-gradient(180deg, rgba(217, 70, 239, 0.20), rgba(12, 10, 26, 0.92))"
    },
    vision: {
      accent: "#f472b6",
      glow: "rgba(244, 114, 182, 0.30)",
      tint: "rgba(244, 114, 182, 0.12)",
      gradient: "linear-gradient(180deg, rgba(236, 72, 153, 0.20), rgba(24, 10, 24, 0.92))"
    },
    operator: {
      accent: "#f59e0b",
      glow: "rgba(245, 158, 11, 0.28)",
      tint: "rgba(245, 158, 11, 0.12)",
      gradient: "linear-gradient(180deg, rgba(245, 158, 11, 0.20), rgba(24, 16, 8, 0.92))"
    }
  }
} as const

export type OrganismSubsystemKey = keyof typeof organismTheme.subsystem

export function getSubsystemTheme(key: OrganismSubsystemKey) {
  return organismTheme.subsystem[key]
}
