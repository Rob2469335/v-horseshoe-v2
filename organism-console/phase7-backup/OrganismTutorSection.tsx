import type { Dispatch, SetStateAction } from "react"
import type { OrganismSubsystem } from "../../features/organism/organism-types"

type ActiveTheme = {
  accent: string
  tint: string
  glow: string
}

type TutorMeta = {
  eyebrow: string
  title: string
  intro: string
}

type TutorialContent = {
  steps: string[]
  operatorAction: string
  deepDive: string[]
}

type OrganismTutorSectionProps = {
  activeCard: OrganismSubsystem
  activeTheme: ActiveTheme
  tutorMeta: TutorMeta
  tutorialContent: TutorialContent
  showDeepTutorial: boolean
  setShowDeepTutorial: Dispatch<SetStateAction<boolean>>
  interactionMode: "observe" | "teach" | "drill"
}

type WatchItem = {
  label: string
  desc: string
}

type Lesson = {
  label: string
  meaning: string
  learn: string
  watch: WatchItem[]
  act: string
  question: string
  answer: string
  next: string
  deep: string[]
}

/**
 * Curated educational content for the tutor panel.
 * These lessons explain the "Why" and "How" in plain English.
 */
const lessons: Record<OrganismSubsystem, Lesson> = {
  learning: {
    label: "The Learning Brain",
    meaning: "How the system builds a memory of what works.",
    learn: "Every time the system acts, it watches the result. If a choice leads to success, it remembers that pattern. If it fails, it updates its 'Policy' (its rulebook) to avoid that mistake again. It's like a student getting better with every homework assignment.",
    watch: [
      { label: "Event Count", desc: "This is the total 'experience' the system has. More events usually mean a smarter system." },
      { label: "Policy Confidence", desc: "How sure the system is about its current rules. If this is low, the system is still 'guessing' a bit." },
      { label: "Learning Loops", desc: "The number of times the system has sat down to 'study' its own logs and improve." }
    ],
    act: "Look at the 'Observed Events' in the grid above. If that number is zero, the system hasn't seen any 'life' yet and can't learn anything!",
    question: "What should I do if learning is slow?",
    answer: "Give the system more tasks! It can only learn by doing. If it's stuck, try changing the environment or checking if the database is recording events correctly.",
    next: "Check the 'Policy Confidence' metric. If it's above 90%, the system is ready for more autonomy.",
    deep: [
      "Uses RLHF (Reinforcement Learning from Human Feedback) logic.",
      "Captures 'traces'—full snapshots of thought vs. outcome.",
      "Updates weights in a local vector database for fast recall."
    ]
  },
  healing: {
    label: "The Healing Reflex",
    meaning: "How the system survives when things break.",
    learn: "Computers crash and servers go down. Healing is the system's 'immune system.' Instead of just stopping, it detects the error, isolates the broken part, and switches to a backup plan automatically.",
    watch: [
      { label: "Recovery Readiness", desc: "Shows if the 'emergency backup' plans are loaded and ready to go." },
      { label: "Active Anomalies", desc: "Strange behaviors the system is investigating right now. Red is bad, Amber is 'watching'." },
      { label: "Rollback Paths", desc: "How many ways the system can 'undo' its last action to stay safe." }
    ],
    act: "Check 'System Ready' status. If it's not green, don't start new tasks. The system is likely busy 'healing' itself.",
    question: "Is 'Self-Healing' always safe?",
    answer: "Mostly. It fixes common errors like network drops. However, if a 'Major Anomaly' appears, you should step in and check the logs manually.",
    next: "If you see '2 Active Anomalies', click the Healing card above to see exactly which parts are struggling.",
    deep: [
      "Uses circuit-breaker patterns to stop cascading failures.",
      "Statistical anomaly detection monitors event flow variance.",
      "Automated rollbacks are triggered by success-rate thresholds."
    ]
  },
  autonomy: {
    label: "Autonomy & Action",
    meaning: "The system's ability to act without asking permission.",
    learn: "Autonomy is the 'hands' of the system. It takes your goals (like 'clean up these files') and decides which tools to use and when. It follows 'Guardrails'—rules you set to make sure it doesn't do anything dangerous.",
    watch: [
      { label: "Autonomous Tasks", desc: "The number of jobs the system is currently doing on its own." },
      { label: "Guardrails", desc: "The safety fences. If these are OFF, the system is in 'Manual' mode only." },
      { label: "Tool Reach", desc: "How many skills (APIs, scripts) the system has permission to use right now." }
    ],
    act: "Make sure 'Guardrails' are active. This is your safety net. If you need to stop the system instantly, use the 'Operator' override.",
    question: "How do I give it more power?",
    answer: "Add more 'Tools' or capabilities to its configuration. The more tools it can reach, the more complex tasks it can handle autonomously.",
    next: "Verify that 'Tool Count' in the grid is higher than zero. A system with no tools has no hands!",
    deep: [
      "Agentic loops: Plan -> Execute -> Observe -> Refine.",
      "Sandboxed execution ensures tools can't escape their permissions.",
      "Token-based capability management restricts sensitive actions."
    ]
  },
  vision: {
    label: "Vision Intelligence",
    meaning: "How the system 'sees' and understands images.",
    learn: "Standard AI only understands text. Vision lets the system 'look' at screenshots, PDFs, or live UI elements. It uses these visual cues to navigate apps just like you do with your eyes.",
    watch: [
      { label: "Primary Model", desc: "The specific 'Vision Brain' currently active. Some are faster, some are smarter." },
      { label: "Vision Tasks", desc: "Current jobs that involve looking at or describing an image." },
      { label: "Runtime Ready", desc: "Confirms the 'eyes' are open and the vision software is connected." }
    ],
    act: "Check 'Vision Live'. If it says 'Pending', the system is blind! You may need to wait for the vision model to finish loading.",
    question: "Why is Vision important?",
    answer: "Because many apps don't have good text descriptions. Vision allows the system to 'see' buttons, icons, and layouts to find its way around.",
    next: "Look at the 'Primary Model'. If it says 'Unknown', the vision system is offline.",
    deep: [
      "Uses Vision-Language Models (VLMs) to convert pixels to tokens.",
      "Multimodal input allows text and images to be processed together.",
      "Visual grounding provides (x, y) coordinates for element interaction."
    ]
  },
  operator: {
    label: "Operator Control",
    meaning: "Your dashboard for steering the entire system.",
    learn: "You are the Captain. This page is your cockpit. Operator control ensures the system is 'Transparent' (you can see what it's thinking) and 'Steerable' (you can change its direction at any time).",
    watch: [
      { label: "Visibility", desc: "Shows if you are getting a 'live feed' of the system's internal thoughts." },
      { label: "Manual Controls", desc: "The buttons and overrides that let you take the wheel if needed." },
      { label: "Trust Posture", desc: "The system's own report on how well it thinks it's following your orders." }
    ],
    act: "Use the 'Interaction Deck' at the very top. Toggle 'Teach' mode to see these lessons, or 'Observe' to just see the raw data.",
    question: "What if I lose control?",
    answer: "Use the 'Manual Override' or simply shut down the backend. The system is designed to be 'Fail-Safe', meaning it stops if it loses contact with you.",
    next: "Switch to 'Drill' mode at the top if you want to focus deeply on just one subsystem.",
    deep: [
      "Role-Based Access Control (RBAC) ensures only you can steer.",
      "Immutable logs provide a perfect 'black box' recording for audits.",
      "WebSocket streaming ensures zero-latency feedback for all actions."
    ]
  }
}

export function OrganismTutorSection({
  activeCard,
  activeTheme,
  tutorMeta,
  tutorialContent,
  showDeepTutorial,
  setShowDeepTutorial,
  interactionMode
}: OrganismTutorSectionProps) {
  const lesson = lessons[activeCard] || lessons.operator
  const isDrill = interactionMode === "drill"
  const isTeach = interactionMode === "teach"

  return (
    <section
      style={{
        display: "grid",
        gridTemplateColumns: "minmax(0, 1.4fr) minmax(360px, 0.8fr)",
        gap: 28,
        alignItems: "start",
        padding: 32,
        borderRadius: 42,
        background: `linear-gradient(180deg, ${activeTheme.tint}, rgba(0,0,0,0.7))`,
        border: `1px solid ${activeTheme.accent}33`,
        boxShadow: `0 40px 100px rgba(0,0,0,0.5), inset 0 0 60px ${activeTheme.glow}`,
        position: "relative",
        overflow: "hidden",
        transition: "all 500ms cubic-bezier(0.16, 1, 0.3, 1)"
      }}
    >
      {/* Visual background element */}
      <div style={{
        position: "absolute",
        top: "-15%",
        right: "-10%",
        width: "60%",
        height: "70%",
        background: `radial-gradient(circle, ${activeTheme.accent}12 0%, transparent 70%)`,
        filter: "blur(50px)",
        pointerEvents: "none"
      }} />

      <div
        key={activeCard}
        style={{
          display: "grid",
          gap: 24,
          padding: 32,
          borderRadius: 32,
          background: "rgba(255,255,255,0.02)",
          backdropFilter: "blur(16px)",
          border: "1px solid rgba(255,255,255,0.06)",
          animation: "tutorReveal 500ms cubic-bezier(0.16, 1, 0.3, 1)"
        }}
      >
        <header style={{ display: "grid", gap: 16 }}>
          <div style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 12,
            width: "fit-content",
            padding: "10px 22px",
            borderRadius: 999,
            background: activeTheme.accent,
            color: "white",
            fontSize: 14,
            fontWeight: 900,
            letterSpacing: "0.14em",
            textTransform: "uppercase",
            boxShadow: `0 10px 28px ${activeTheme.accent}55`
          }}>
            <span style={{
              width: 10,
              height: 10,
              borderRadius: "50%",
              background: "white",
              boxShadow: "0 0 16px white",
              animation: "statusBlink 1.4s ease-in-out infinite"
            }} />
            {tutorMeta.eyebrow}
          </div>

          <div style={{ display: "grid", gap: 8 }}>
            <h2 style={{ margin: 0, color: "white", fontSize: 44, fontWeight: 900, letterSpacing: "-0.04em", lineHeight: 1 }}>
              {lesson.label}
            </h2>
            <p style={{ margin: 0, color: activeTheme.accent, fontSize: 20, fontWeight: 700, letterSpacing: "-0.01em" }}>
              {lesson.meaning}
            </p>
          </div>
        </header>

        {/* SUMMARY SECTION - Always visible in Teach/Drill */}
        {(isTeach || isDrill) && (
          <article style={{
            borderRadius: 26,
            padding: 26,
            background: `linear-gradient(135deg, ${activeTheme.accent}15, ${activeTheme.accent}05)`,
            border: `1px solid ${activeTheme.accent}22`,
            boxShadow: `inset 0 0 30px ${activeTheme.accent}08`
          }}>
            <div style={{ color: activeTheme.accent, fontSize: 13, fontWeight: 900, textTransform: "uppercase", letterSpacing: "0.22em", marginBottom: 14 }}>
              Fundamental Concept
            </div>
            <div style={{ color: "white", lineHeight: 1.7, fontSize: 17, fontWeight: 400 }}>
              {tutorialContent.steps[0] || lesson.learn}
            </div>
          </article>
        )}

        {/* WATCH SECTION - Visible in Drill or if toggled */}
        {(isDrill || showDeepTutorial) && (
          <div style={{ display: "grid", gap: 18, animation: "tutorReveal 400ms ease-out" }}>
            <div style={{ color: "rgba(255,255,255,0.4)", fontSize: 13, fontWeight: 900, letterSpacing: "0.22em", textTransform: "uppercase" }}>
              Metrics Interpretation
            </div>

            <div style={{ display: "grid", gap: 14 }}>
              {lesson.watch.map((item, index) => (
                <div
                  key={index}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "52px 1fr",
                    gap: 22,
                    alignItems: "center",
                    padding: "20px 24px",
                    borderRadius: 22,
                    background: "rgba(255,255,255,0.03)",
                    border: "1px solid rgba(255,255,255,0.06)",
                    transition: "all 0.3s cubic-bezier(0.16, 1, 0.3, 1)"
                  }}
                >
                  <div style={{
                    width: 52,
                    height: 52,
                    borderRadius: 16,
                    display: "grid",
                    placeItems: "center",
                    background: activeTheme.tint,
                    color: activeTheme.accent,
                    fontSize: 24,
                    fontWeight: 900,
                    boxShadow: `0 12px 28px ${activeTheme.glow}`
                  }}>
                    {index + 1}
                  </div>
                  <div>
                    <div style={{ color: "white", fontWeight: 800, fontSize: 17, marginBottom: 4 }}>{item.label}</div>
                    <div style={{ color: "rgba(236,243,255,0.6)", fontSize: 14, lineHeight: 1.55 }}>{item.desc}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ACT SECTION - Visible in Teach/Drill */}
        {(isTeach || isDrill) && (
          <article style={{
            borderRadius: 26,
            padding: 26,
            background: "rgba(255,255,255,0.03)",
            border: "1px solid rgba(255,255,255,0.08)",
            boxShadow: `inset 4px 0 0 ${activeTheme.accent}`
          }}>
            <div style={{ color: activeTheme.accent, fontSize: 13, fontWeight: 900, textTransform: "uppercase", letterSpacing: "0.22em", marginBottom: 14 }}>
              Operator Execution
            </div>
            <div style={{ color: "white", lineHeight: 1.6, fontSize: 16, marginBottom: 16, fontWeight: 600 }}>
              {lesson.act}
            </div>
            <div style={{ 
              color: "rgba(236,243,255,0.75)", 
              fontSize: 14, 
              padding: "14px 18px", 
              background: "rgba(0,0,0,0.4)", 
              borderRadius: 14,
              border: "1px solid rgba(255,255,255,0.05)",
              lineHeight: 1.5
            }}>
              <strong>Context-Aware Guide:</strong> {tutorialContent.operatorAction}
            </div>
          </article>
        )}
      </div>

      <div style={{ display: "grid", gap: 28 }}>
        {/* QUESTION SECTION - Visible in Teach/Drill */}
        {(isTeach || isDrill) && (
          <article style={{
            borderRadius: 34,
            padding: 32,
            background: "linear-gradient(180deg, rgba(255,255,255,0.08), rgba(255,255,255,0.02))",
            border: "1px solid rgba(255,255,255,0.12)",
            boxShadow: "0 28px 60px rgba(0,0,0,0.4)"
          }}>
            <div style={{ color: activeTheme.accent, fontSize: 12, fontWeight: 900, textTransform: "uppercase", letterSpacing: "0.2em", marginBottom: 20 }}>
              FAQ & Protocol
            </div>
            <div style={{ color: "white", fontSize: 26, fontWeight: 800, lineHeight: 1.2, marginBottom: 18 }}>
              &ldquo;{lesson.question}&rdquo;
            </div>
            <div style={{ color: "rgba(236,243,255,0.9)", lineHeight: 1.7, fontSize: 16, marginBottom: 24 }}>
              {lesson.answer}
            </div>
            <div style={{ 
              color: "white", 
              fontSize: 14, 
              fontWeight: 800, 
              padding: "16px", 
              background: `${activeTheme.accent}22`, 
              borderRadius: 16,
              textAlign: "center",
              border: `1px solid ${activeTheme.accent}33`,
              boxShadow: `0 8px 20px ${activeTheme.accent}11`
            }}>
              <span style={{ color: activeTheme.accent, marginRight: 8 }}>Next:</span>
              {lesson.next}
            </div>
          </article>
        )}

        {/* DEEPER LESSON SECTION - Visible in Drill */}
        {(isDrill || showDeepTutorial) && (
          <article style={{
            borderRadius: 34,
            padding: 32,
            background: "rgba(0,0,0,0.5)",
            border: "1px solid rgba(255,255,255,0.08)",
            animation: "tutorReveal 500ms ease-out"
          }}>
            <div style={{ color: activeTheme.accent, fontSize: 12, fontWeight: 900, textTransform: "uppercase", letterSpacing: "0.22em", marginBottom: 24 }}>
              Architectural Traces
            </div>

            <div style={{ display: "grid", gap: 16 }}>
              {[...lesson.deep, ...(tutorialContent.deepDive || [])].slice(0, 4).map((item, index) => (
                <div
                  key={index}
                  style={{
                    borderRadius: 18,
                    padding: 18,
                    background: "rgba(255,255,255,0.04)",
                    border: "1px solid rgba(255,255,255,0.06)",
                    color: "rgba(236,243,255,0.95)",
                    lineHeight: 1.5,
                    fontSize: 15,
                    display: "flex",
                    gap: 16
                  }}
                >
                  <span style={{ color: activeTheme.accent, fontWeight: 900 }}>•</span>
                  {item}
                </div>
              ))}
            </div>
          </article>
        )}

        {isTeach && (
          <button
            type="button"
            aria-expanded={showDeepTutorial}
            onClick={() => setShowDeepTutorial((v) => !v)}
            style={{
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              padding: "22px 36px",
              borderRadius: 22,
              border: `1px solid ${activeTheme.accent}66`,
              background: showDeepTutorial ? activeTheme.accent : "rgba(255,255,255,0.04)",
              color: "white",
              fontSize: 16,
              fontWeight: 900,
              letterSpacing: "0.1em",
              textTransform: "uppercase",
              cursor: "pointer",
              boxShadow: showDeepTutorial ? `0 18px 45px ${activeTheme.accent}66` : "none",
              transform: showDeepTutorial ? "translateY(-2px)" : "translateY(0)",
              transition: "all 300ms cubic-bezier(0.16, 1, 0.3, 1)"
            }}
          >
            {showDeepTutorial ? "← Simplify View" : "Explore Deep Mechanics →"}
          </button>
        )}
      </div>
    </section>
  )
}
