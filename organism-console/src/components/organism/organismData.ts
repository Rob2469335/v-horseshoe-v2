export const organismSubsystems = [
  {
    id: "learning",
    label: "Self-Learning Core",
    shortLabel: "Learning",
    color: "#61f3ff",
    glow: "rgba(97,243,255,0.35)",
    accent: "#1677ff",
    description: "Turns events into improved future behavior through outcome capture, policy tuning, and pattern reuse.",
    beginnerSummary: "This area helps the system learn from what just happened so it can make better decisions next time.",
    operatorPrompt: "Check whether recent outcomes are turning into better routing and tool choice.",
    metrics: [
      { label: "Observed events", value: "6,802", tone: "cyan" },
      { label: "Learning loops", value: "18", tone: "blue" },
      { label: "Policy confidence", value: "91%", tone: "green" }
    ]
  },
  {
    id: "healing",
    label: "Self-Healing Layer",
    shortLabel: "Healing",
    color: "#7dffb3",
    glow: "rgba(125,255,179,0.32)",
    accent: "#18c964",
    description: "Detects instability, isolates regressions, and restores safer behavior before operator trust degrades.",
    beginnerSummary: "This area watches for breakage and helps the system recover instead of just failing.",
    operatorPrompt: "Look for rollback readiness, degraded modules, and whether protection pathways are active.",
    metrics: [
      { label: "Recovery readiness", value: "High", tone: "green" },
      { label: "Rollback paths", value: "9", tone: "emerald" },
      { label: "Active anomalies", value: "2", tone: "amber" }
    ]
  },
  {
    id: "autonomy",
    label: "Autonomy Engine",
    shortLabel: "Autonomy",
    color: "#c38bff",
    glow: "rgba(195,139,255,0.34)",
    accent: "#8b5cf6",
    description: "Plans, chooses, and executes with bounded independence while remaining reviewable and interruptible.",
    beginnerSummary: "This area is the system acting on its own within rules you can still monitor and control.",
    operatorPrompt: "Verify bounded execution, intervention points, and task routing quality.",
    metrics: [
      { label: "Autonomous tasks", value: "34", tone: "violet" },
      { label: "Guardrails", value: "On", tone: "green" },
      { label: "Intervention points", value: "6", tone: "pink" }
    ]
  },
  {
    id: "vision",
    label: "Vision Intelligence",
    shortLabel: "Vision",
    color: "#ff8de1",
    glow: "rgba(255,141,225,0.33)",
    accent: "#ec4899",
    description: "Adds visual understanding so the organism can inspect interfaces, screenshots, and multimodal evidence.",
    beginnerSummary: "This area helps the system see screens and images instead of relying only on text.",
    operatorPrompt: "Confirm active model, visual routing, and confidence before acting on image-based evidence.",
    metrics: [
      { label: "Primary model", value: "qwen3-vl:8b", tone: "pink" },
      { label: "Fallback path", value: "Ready", tone: "violet" },
      { label: "Vision tasks", value: "11", tone: "cyan" }
    ]
  },
  {
    id: "memory",
    label: "Memory Fabric",
    shortLabel: "Memory",
    color: "#ffd76b",
    glow: "rgba(255,215,107,0.30)",
    accent: "#f59e0b",
    description: "Stores traces, outcomes, patterns, and operational context so the organism can act with continuity.",
    beginnerSummary: "This area helps the system remember useful history instead of starting from zero every time.",
    operatorPrompt: "Track whether retrieval is supporting better actions without surfacing stale context.",
    metrics: [
      { label: "Memory hits", value: "84%", tone: "amber" },
      { label: "Cached tools", value: "11", tone: "gold" },
      { label: "Retention health", value: "Stable", tone: "green" }
    ]
  },
  {
    id: "operator",
    label: "Human Oversight",
    shortLabel: "Operator",
    color: "#ff8f72",
    glow: "rgba(255,143,114,0.32)",
    accent: "#f97316",
    description: "Keeps the organism accountable, interruptible, and understandable for humans supervising live behavior.",
    beginnerSummary: "This area makes sure a person can still understand, approve, and step in when needed.",
    operatorPrompt: "Keep visibility high, explanations clear, and override posture obvious.",
    metrics: [
      { label: "Visibility", value: "Full", tone: "orange" },
      { label: "Manual controls", value: "Ready", tone: "green" },
      { label: "Trust posture", value: "Observed", tone: "cyan" }
    ]
  }
] as const;
