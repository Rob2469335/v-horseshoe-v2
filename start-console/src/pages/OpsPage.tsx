import { useEffect, useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { api } from "../lib/api"
import {
  automationCatalog,
  basicAutomations,
  scaryAutomations,
  seniorAutomations,
  starterAutomations
} from "../lib/automation-catalog"
import type {
  HealthResponse
} from "../lib/types"
import { useUiStore } from "../state/ui-store"
import { AutomationRunner } from "../components/organism/AutomationRunner"

export default function OpsPage() {
  const backendUrl = useUiStore((state) => state.backendUrl)
  const setConnectionStatus = useUiStore((state) => state.setConnectionStatus)

  const [selectedAutomationId, setSelectedAutomationId] = useState<string>(starterAutomations[0]?.id ?? "")

  const [upworkInput, setUpworkInput] = useState<string>("")
  const [upworkResult, setUpworkResult] = useState<any>(null)
  const [upworkLoading, setUpworkLoading] = useState<boolean>(false)
  const [activeAction, setActiveAction] = useState<string>("")

  const runUpworkAction = async (action: string) => {
    if (!upworkInput.trim()) {
      alert("Please enter some job or project text first.")
      return
    }
    setUpworkLoading(true)
    setActiveAction(action)
    setUpworkResult(null)
    try {
      const endpoint = `${backendUrl}/upwork/${action}`
      const res = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ input: upworkInput })
      })
      if (!res.ok) {
        throw new Error(`Server returned status ${res.status}`)
      }
      const data = await res.json()
      setUpworkResult(data)
    } catch (err: any) {
      setUpworkResult({ error: err.message || "Failed to fetch response" })
    } finally {
      setUpworkLoading(false)
    }
  }

  const renderUpworkResult = () => {
    if (upworkLoading) {
      return (
        <div style={{ padding: "20px", textAlign: "center", color: "var(--text-muted)" }}>
          <span>⏳ Synthesizing {activeAction.toUpperCase()} analysis...</span>
        </div>
      )
    }
    if (!upworkResult) return null

    if (upworkResult.error) {
      return (
        <div style={{ padding: "12px", background: "rgba(239, 68, 68, 0.08)", border: "1px solid rgba(239, 68, 68, 0.3)", borderRadius: "10px", color: "#f87171", marginTop: "12px" }}>
          <strong>Error:</strong> {upworkResult.error}
        </div>
      )
    }

    switch (upworkResult.type) {
      case "proposal":
        return (
          <div style={{ marginTop: "16px", padding: "16px", background: "rgba(20, 27, 41, 0.4)", border: "1px solid rgba(255, 255, 255, 0.08)", borderRadius: "14px" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
              <strong style={{ color: "var(--page-accent)" }}>Generated Cover Letter Proposal</strong>
              <button 
                className="topbar__button" 
                style={{ padding: "6px 12px", fontSize: "12px", border: "1px solid var(--page-accent)", background: "var(--page-accent-tint)", color: "#fff", minHeight: "auto", height: "auto" }}
                onClick={() => {
                  navigator.clipboard.writeText(upworkResult.content)
                  alert("Copied to clipboard!")
                }}
              >
                Copy Cover Letter
              </button>
            </div>
            <pre style={{ whiteSpace: "pre-wrap", fontFamily: "inherit", color: "var(--text-soft)", fontSize: "14px", margin: 0, background: "rgba(0,0,0,0.25)", padding: "12px", borderRadius: "8px", border: "1px solid rgba(255,255,255,0.04)" }}>{upworkResult.content}</pre>
            <div style={{ marginTop: "12px", fontSize: "11px", color: "var(--text-muted)" }}>
              Memory nodes queried: {upworkResult.memory_used} | Generated at: {new Date(upworkResult.timestamp).toLocaleString()}
            </div>
          </div>
        )
      case "estimate":
        return (
          <div style={{ marginTop: "16px", display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
            <div style={{ padding: "12px", background: "rgba(16, 185, 129, 0.06)", border: "1px solid rgba(16, 185, 129, 0.2)", borderRadius: "12px" }}>
              <div style={{ fontSize: "11px", color: "var(--success)", fontWeight: "bold", textTransform: "uppercase", letterSpacing: "0.05em" }}>Hours Estimate</div>
              <div style={{ fontSize: "20px", fontWeight: "bold", color: "#34d399", marginTop: "4px" }}>{upworkResult.hours} hours</div>
            </div>
            <div style={{ padding: "12px", background: "rgba(59, 130, 246, 0.06)", border: "1px solid rgba(59, 130, 246, 0.2)", borderRadius: "12px" }}>
              <div style={{ fontSize: "11px", color: "var(--info)", fontWeight: "bold", textTransform: "uppercase", letterSpacing: "0.05em" }}>Recommended Bid Range</div>
              <div style={{ fontSize: "20px", fontWeight: "bold", color: "#60a5fa", marginTop: "4px" }}>{upworkResult.bid}</div>
            </div>
            <div style={{ gridColumn: "span 2", padding: "12px", background: "rgba(20, 27, 41, 0.3)", border: "1px solid rgba(255, 255, 255, 0.06)", borderRadius: "12px" }}>
              <strong style={{ color: "var(--text)", fontSize: "13px" }}>Bidding Strategy & Analysis:</strong>
              <p style={{ margin: "6px 0 0 0", color: "var(--text-soft)", fontSize: "14px", lineHeight: "1.6" }}>{upworkResult.analysis}</p>
            </div>
          </div>
        )
      case "scope_breakdown":
        return (
          <div style={{ marginTop: "16px", padding: "16px", background: "rgba(245, 158, 11, 0.05)", border: "1px solid rgba(245, 158, 11, 0.2)", borderRadius: "14px" }}>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "12px", alignItems: "center" }}>
              <strong style={{ color: "#fbbf24" }}>Suggested Scope Roadmap</strong>
              <span style={{ fontSize: "12px", background: "rgba(245, 158, 11, 0.15)", color: "#fbbf24", padding: "2px 8px", borderRadius: "99px", fontWeight: "bold" }}>Est. Duration: {upworkResult.estimate}</span>
            </div>
            <ul style={{ margin: 0, paddingLeft: "20px", color: "var(--text-soft)", display: "grid", gap: "6px" }}>
              {(upworkResult.items || []).map((item: string, i: number) => (
                <li key={i} style={{ fontSize: "14px" }}>{item}</li>
              ))}
            </ul>
          </div>
        )
      case "invoice":
        return (
          <div style={{ marginTop: "16px", padding: "16px", background: "rgba(236, 72, 153, 0.05)", border: "1px solid rgba(236, 72, 153, 0.2)", borderRadius: "14px" }}>
            <strong style={{ color: "#f472b6", display: "block", marginBottom: "12px" }}>Draft Invoice Summary</strong>
            <pre style={{ whiteSpace: "pre-wrap", fontFamily: "inherit", color: "var(--text-soft)", fontSize: "14px", margin: "0 0 12px 0", background: "rgba(0,0,0,0.25)", padding: "12px", borderRadius: "8px", border: "1px solid rgba(255,255,255,0.04)" }}>{upworkResult.summary}</pre>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", color: "#f472b6", fontSize: "15px", borderTop: "1px solid rgba(236, 72, 153, 0.2)", paddingTop: "12px" }}>
              <span>Total Estimated Bid:</span>
              <strong style={{ fontSize: "20px", color: "#ec4899" }}>{upworkResult.total}</strong>
            </div>
          </div>
        )
      case "case_study":
        return (
          <div style={{ marginTop: "16px", padding: "16px", background: "rgba(20, 184, 166, 0.05)", border: "1px solid rgba(20, 184, 166, 0.2)", borderRadius: "14px" }}>
            <strong style={{ color: "#2dd4bf", display: "block", marginBottom: "12px" }}>Pitch Case Study Bullets</strong>
            <ul style={{ margin: 0, paddingLeft: "20px", color: "var(--text-soft)", display: "grid", gap: "6px" }}>
              {(upworkResult.bullets || []).map((bullet: string, i: number) => (
                <li key={i} style={{ fontSize: "14px" }}>{bullet}</li>
              ))}
            </ul>
          </div>
        )
      case "gap_analysis":
        return (
          <div style={{ marginTop: "16px", padding: "16px", background: "rgba(239, 68, 68, 0.05)", border: "1px solid rgba(239, 68, 68, 0.2)", borderRadius: "14px" }}>
            <strong style={{ color: "#f87171", display: "block", marginBottom: "8px" }}>Skills Gap Audit</strong>
            <p style={{ margin: "0 0 12px 0", fontSize: "13px", color: "var(--text-soft)" }}>Identified competencies required for this contract:</p>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "8px" }}>
              {(upworkResult.missing || []).map((skill: string, i: number) => (
                <span key={i} style={{ background: "rgba(239, 68, 68, 0.15)", color: "#f87171", border: "1px solid rgba(239, 68, 68, 0.3)", padding: "4px 10px", borderRadius: "99px", fontSize: "12px", fontWeight: "bold" }}>{skill}</span>
              ))}
            </div>
          </div>
        )
      default:
        return (
          <pre style={{ marginTop: "16px", padding: "12px", background: "rgba(2, 8, 20, 0.62)", border: "1px solid rgba(255, 255, 255, 0.07)", borderRadius: "14px", overflow: "auto" }}>{JSON.stringify(upworkResult, null, 2)}</pre>
        )
    }
  }

  const healthQuery = useQuery({
    queryKey: ["health", backendUrl],
    queryFn: () => api.getHealth<HealthResponse>(backendUrl),
    retry: 1,
    refetchInterval: 30000
  })

  useEffect(() => {
    if (healthQuery.isSuccess) {
      setConnectionStatus("online")
      return
    }

    if (healthQuery.isError) {
      setConnectionStatus("offline")
      return
    }

    if (healthQuery.isLoading) {
      setConnectionStatus("connecting")
    }
  }, [
    healthQuery.isSuccess,
    healthQuery.isError,
    healthQuery.isLoading,
    setConnectionStatus
  ])

  const selectedAutomation = useMemo(() => {
    return automationCatalog.find((item) => item.id === selectedAutomationId) ?? starterAutomations[0]
  }, [selectedAutomationId])

  return (
    <section className="page page--ops">
      <div className="tutor-hero">
        <div>
          <h1>Automation Tutor</h1>
          <p>
            Learn what each automation does, why it matters, and how to use it one step at a time.
          </p>
        </div>

        <div className="tutor-hero__stats">
          <div className="tutor-stat">
            <span className="tutor-stat__label">Automations</span>
            <strong>{automationCatalog.length}</strong>
          </div>
          <div className="tutor-stat">
            <span className="tutor-stat__label">Starter</span>
            <strong>{starterAutomations.length}</strong>
          </div>
          <div className="tutor-stat">
            <span className="tutor-stat__label">Backend</span>
            <strong>{healthQuery.data?.status ?? "Loading"}</strong>
          </div>
        </div>
      </div>

      <div className="tutor-layout">
        <article className="ops-panel tutor-panel tutor-panel--catalog">
          <h2>Start here</h2>
          <p className="tutor-panel__intro">
            Pick one automation to learn. Start with the Robert tools, then explore senior help, scary situations, and basic computer help.
          </p>

          <div className="tutor-group">
            <h3>Made for Robert</h3>

{/* UPWORK AGENT BLOCK */}
<div className="tutor-group" style={{ marginBottom: "28px" }}>
  <h3>Upwork Agent (Tier System)</h3>
  <p className="tutor-panel__intro" style={{ marginBottom: "14px" }}>
    Paste a job description or client request below, and choose a bidding strategist action to execute.
  </p>

  <textarea
    className="agent-textarea"
    style={{
      width: "100%",
      minHeight: "120px",
      padding: "14px",
      borderRadius: "14px",
      fontFamily: "inherit",
      fontSize: "14px",
      resize: "vertical",
      marginBottom: "14px",
      boxSizing: "border-box"
    }}
    placeholder="Paste Upwork job posting description here..."
    value={upworkInput}
    onChange={(e) => setUpworkInput(e.target.value)}
  />

  <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "8px" }}>
    {[
      { action: "propose", label: "Propose Letter" },
      { action: "rate", label: "Bid Heuristics" },
      { action: "pitch", label: "Case Study Pitch" },
      { action: "scope", label: "Scope Roadmap" },
      { action: "invoice", label: "Draft Invoice" },
      { action: "skills-gap", label: "Skills Audit" }
    ].map((item) => (
      <button
        key={item.action}
        type="button"
        className="topbar__button"
        style={{
          cursor: upworkLoading ? "not-allowed" : "pointer",
          padding: "10px",
          textAlign: "center",
          fontWeight: "600",
          fontSize: "13px",
          borderColor: activeAction === item.action ? "var(--page-accent)" : "rgba(255,255,255,0.09)",
          background: activeAction === item.action ? "var(--page-accent-tint)" : "linear-gradient(180deg, rgba(13,18,29,0.92), rgba(13,18,29,0.82))",
          color: activeAction === item.action ? "white" : "var(--text-soft)",
          boxShadow: activeAction === item.action ? "0 0 12px var(--page-accent-glow)" : "none"
        }}
        disabled={upworkLoading}
        onClick={() => runUpworkAction(item.action)}
      >
        {item.label}
      </button>
    ))}
  </div>

  {renderUpworkResult()}
</div>

            <div className="tutor-card-list">
              {starterAutomations.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={`tutor-card${item.id === selectedAutomation?.id ? " tutor-card--active" : ""}`}
                  onClick={() => setSelectedAutomationId(item.id)}
                >
                  <span className="tutor-card__title">{item.title}</span>
                  <span className="tutor-card__meta">{item.category} · {item.difficulty}</span>
                  <span className="tutor-card__text">{item.plainEnglish}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="tutor-group">
            <h3>Helpful for seniors</h3>
            <div className="tutor-card-list">
              {seniorAutomations.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={`tutor-card${item.id === selectedAutomation?.id ? " tutor-card--active" : ""}`}
                  onClick={() => setSelectedAutomationId(item.id)}
                >
                  <span className="tutor-card__title">{item.title}</span>
                  <span className="tutor-card__meta">{item.category} · {item.difficulty}</span>
                  <span className="tutor-card__text">{item.plainEnglish}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="tutor-group">
            <h3>Scary situations</h3>
            <div className="tutor-card-list">
              {scaryAutomations.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={`tutor-card${item.id === selectedAutomation?.id ? " tutor-card--active" : ""}`}
                  onClick={() => setSelectedAutomationId(item.id)}
                >
                  <span className="tutor-card__title">{item.title}</span>
                  <span className="tutor-card__meta">{item.category} · {item.difficulty}</span>
                  <span className="tutor-card__text">{item.plainEnglish}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="tutor-group">
            <h3>Basic computer help</h3>
            <div className="tutor-card-list">
              {basicAutomations.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={`tutor-card${item.id === selectedAutomation?.id ? " tutor-card--active" : ""}`}
                  onClick={() => setSelectedAutomationId(item.id)}
                >
                  <span className="tutor-card__title">{item.title}</span>
                  <span className="tutor-card__meta">{item.category} · {item.difficulty}</span>
                  <span className="tutor-card__text">{item.plainEnglish}</span>
                </button>
              ))}
            </div>
          </div>
        </article>

        <article className="ops-panel tutor-panel tutor-panel--lesson">
          {selectedAutomation ? (
            <>
              <div className="lesson-header">
                <div>
                  <h2>{selectedAutomation.title}</h2>
                  <p className="lesson-header__text">{selectedAutomation.plainEnglish}</p>
                </div>
                <div className="lesson-badges">
                  <span className="lesson-badge">{selectedAutomation.group}</span>
                  <span className="lesson-badge">{selectedAutomation.category}</span>
                  <span className="lesson-badge">{selectedAutomation.difficulty}</span>
                </div>
              </div>

              <div className="lesson-section">
                <h3>What this means</h3>
                <p>{selectedAutomation.whatItMeans}</p>
              </div>

              <div className="lesson-section">
                <h3>Why this matters</h3>
                <p>{selectedAutomation.whyThisMatters}</p>
              </div>

              <div className="lesson-section">
                <h3>Words to know</h3>
                <div className="lesson-glossary">
                  {selectedAutomation.wordsToKnow.map((item) => (
                    <article key={`${selectedAutomation.id}-${item.term}`} className="lesson-glossary__item">
                      <strong>{item.term}</strong>
                      <p>{item.meaning}</p>
                    </article>
                  ))}
                </div>
              </div>

              <div className="lesson-grid">
                <div className="lesson-section">
                  <h3>Before you start</h3>
                  <ul>
                    {selectedAutomation.beforeYouStart.map((item) => (
                      <li key={`${selectedAutomation.id}-before-${item}`}>{item}</li>
                    ))}
                  </ul>
                </div>

                <div className="lesson-section">
                  <h3>Inputs</h3>
                  <ul>
                    {selectedAutomation.inputs.map((item) => (
                      <li key={`${selectedAutomation.id}-input-${item}`}>{item}</li>
                    ))}
                  </ul>
                </div>
              </div>

              <div className="lesson-section">
                <h3>Steps</h3>
                <ol>
                  {selectedAutomation.steps.map((item) => (
                    <li key={`${selectedAutomation.id}-step-${item}`}>{item}</li>
                  ))}
                </ol>
              </div>

              <div className="lesson-grid">
                <div className="lesson-section">
                  <h3>What success looks like</h3>
                  <ul>
                    {selectedAutomation.whatSuccessLooksLike.map((item) => (
                      <li key={`${selectedAutomation.id}-success-${item}`}>{item}</li>
                    ))}
                  </ul>
                </div>

                <div className="lesson-section">
                  <h3>When to ask for help</h3>
                  <ul>
                    {selectedAutomation.whenToAskForHelp.map((item) => (
                      <li key={`${selectedAutomation.id}-help-${item}`}>{item}</li>
                    ))}
                  </ul>
                </div>
              </div>

              <div className="lesson-grid">
                <div className="lesson-section">
                  <h3>Outputs</h3>
                  <ul>
                    {selectedAutomation.outputs.map((item) => (
                      <li key={`${selectedAutomation.id}-output-${item}`}>{item}</li>
                    ))}
                  </ul>
                </div>

                <div className="lesson-section">
                  <h3>Common mistakes</h3>
                  <ul>
                    {selectedAutomation.commonMistakes.map((item) => (
                      <li key={`${selectedAutomation.id}-mistake-${item}`}>{item}</li>
                    ))}
                  </ul>
                </div>
              </div>

              <div className="lesson-section">
                <h3>Example</h3>
                <p>{selectedAutomation.example}</p>
              </div>
              <AutomationRunner
                backendUrl={backendUrl}
                automationId={selectedAutomation.id}
                automationTitle={selectedAutomation.title}
                prompt={(selectedAutomation as any).prompt ?? `You are a helpful AI assistant. Complete this task: ${selectedAutomation.plainEnglish}. User input: {input}`}
                example={selectedAutomation.example}
                inputs={selectedAutomation.inputs}
              />
            </>
          ) : (
            <div className="trace-empty">Select an automation to start learning.</div>
          )}
        </article>
      </div>

    </section>
  )
}







