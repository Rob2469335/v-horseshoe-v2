import { useEffect, useMemo, useState, type ReactNode } from "react"
import { useQuery } from "@tanstack/react-query"
import { api } from "../lib/api"
import {
  automationCatalog,
  basicAutomations,
  scaryAutomations,
  seniorAutomations,
  starterAutomations
} from "../lib/automation-catalog"
import type { HealthResponse } from "../lib/types"
import { useUiStore } from "../state/ui-store"
import { AutomationRunner } from "../components/organism/AutomationRunner"
import DiffReviewPanel from "../components/organism/DiffReviewPanel"

function AutomationGroup({
  title,
  items,
  selectedId,
  onSelect
}: {
  title: string
  items: typeof starterAutomations
  selectedId: string
  onSelect: (id: string) => void
}) {
  return (
    <div className="flex flex-col gap-4">
      <h3 className="text-sm font-bold text-white uppercase tracking-wider">{title}</h3>
      <div className="flex flex-col gap-3">
        {items.map((item) => {
          const isActive = item.id === selectedId;
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => onSelect(item.id)}
              className={`p-4 rounded-xl border text-left transition-all flex flex-col gap-1.5 ${
                isActive
                  ? "bg-cyan-900/20 border-cyan-500/50 shadow-[0_0_15px_rgba(34,211,238,0.1)]"
                  : "bg-slate-800/30 border-white/5 hover:bg-slate-800/60 hover:border-white/20"
              }`}
            >
              <span className={`font-bold ${isActive ? "text-cyan-300" : "text-white"}`}>{item.title}</span>
              <span className="text-[10px] uppercase tracking-widest text-slate-500 font-bold">{item.category} · {item.difficulty}</span>
              <span className="text-xs text-slate-400 line-clamp-2">{item.plainEnglish}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}

function LessonSection({
  title,
  accent = "text-white",
  children
}: {
  title: string
  accent?: string
  children: ReactNode
}) {
  return (
    <div className="flex flex-col gap-2">
      <h3 className={`text-sm font-bold uppercase tracking-widest ${accent}`}>{title}</h3>
      {children}
    </div>
  )
}

function LessonCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-3 bg-slate-900/40 p-5 rounded-xl border border-white/5">
      <h3 className="text-sm font-bold text-white uppercase tracking-widest">{title}</h3>
      {children}
    </div>
  )
}

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
    <section className="flex flex-col h-full w-full overflow-hidden p-6 text-slate-300">
      {/* Header / Hero */}
      <div className="flex justify-between items-center bg-[#04080f]/60 border border-white/5 backdrop-blur-xl p-6 rounded-2xl mb-6 shadow-[0_0_30px_rgba(0,0,0,0.5)]">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight flex items-center gap-3">
            <span className="w-2 h-2 rounded-full bg-cyan-400 shadow-[0_0_10px_#22d3ee]"></span>
            Automation Tutor & Ops
          </h1>
          <p className="text-sm text-slate-400 mt-2">
            Learn what each automation does, why it matters, and how to use it one step at a time.
          </p>
        </div>

        <div className="flex gap-6 bg-slate-900/50 p-4 rounded-xl border border-white/5">
          <div className="flex flex-col">
            <span className="text-xs uppercase tracking-widest text-slate-500 font-bold">Automations</span>
            <strong className="text-xl text-white font-mono">{automationCatalog.length}</strong>
          </div>
          <div className="w-px bg-white/10" />
          <div className="flex flex-col">
            <span className="text-xs uppercase tracking-widest text-slate-500 font-bold">Starter</span>
            <strong className="text-xl text-white font-mono">{starterAutomations.length}</strong>
          </div>
          <div className="w-px bg-white/10" />
          <div className="flex flex-col">
            <span className="text-xs uppercase tracking-widest text-slate-500 font-bold">Backend</span>
            <strong className="text-xl text-cyan-400 font-mono capitalize">{healthQuery.data?.status ?? "Loading"}</strong>
          </div>
        </div>
      </div>

      {/* Main Layout Grid */}
      <div className="flex gap-6 h-full min-h-0 overflow-hidden">

        {/* Left Column: Catalog */}
        <article className="w-1/3 flex flex-col overflow-y-auto pr-2 gap-8 custom-scrollbar pb-10">

          {/* LIVE DIFF REVIEW — what the agents changed */}
          <DiffReviewPanel backendUrl={backendUrl} />

          {/* UPWORK AGENT BLOCK */}
          <div className="flex flex-col gap-3 bg-[#04080f]/40 border border-cyan-500/20 p-5 rounded-2xl shadow-[inset_0_0_20px_rgba(34,211,238,0.05)]">
            <h3 className="text-sm font-bold text-cyan-400 uppercase tracking-wider">Upwork Agent (Tier System)</h3>
            <p className="text-xs text-slate-400">
              Paste a job description or client request below, and choose a bidding strategist action to execute.
            </p>

            <textarea
              className="w-full min-h-[120px] p-4 rounded-xl bg-slate-900/60 border border-white/10 text-sm text-slate-200 focus:outline-none focus:border-cyan-500/50 focus:ring-1 focus:ring-cyan-500/50 transition-all resize-y"
              placeholder="Paste Upwork job posting description here..."
              value={upworkInput}
              onChange={(e) => setUpworkInput(e.target.value)}
            />

            <div className="grid grid-cols-2 lg:grid-cols-3 gap-2 mt-2">
              {[
                { action: "propose", label: "Propose Letter" },
                { action: "rate", label: "Bid Heuristics" },
                { action: "pitch", label: "Case Study Pitch" },
                { action: "scope", label: "Scope Roadmap" },
                { action: "invoice", label: "Draft Invoice" },
                { action: "skills-gap", label: "Skills Audit" }
              ].map((item) => {
                const isActive = activeAction === item.action;
                return (
                  <button
                    key={item.action}
                    type="button"
                    disabled={upworkLoading}
                    onClick={() => runUpworkAction(item.action)}
                    className={`px-3 py-2.5 rounded-lg text-xs font-semibold transition-all ${
                      isActive
                        ? "bg-cyan-500/20 border-cyan-400/50 text-cyan-300 shadow-[0_0_15px_rgba(34,211,238,0.2)] border"
                        : "bg-slate-800/40 border-white/5 text-slate-400 hover:bg-slate-800/80 hover:text-slate-200 border"
                    } ${upworkLoading ? "opacity-50 cursor-not-allowed" : "cursor-pointer"}`}
                  >
                    {item.label}
                  </button>
                )
              })}
            </div>

            {renderUpworkResult()}
          </div>

          <AutomationGroup
            title="Made for Robert"
            items={starterAutomations}
            selectedId={selectedAutomation?.id ?? ""}
            onSelect={setSelectedAutomationId}
          />

          <AutomationGroup
            title="Helpful for Seniors"
            items={seniorAutomations}
            selectedId={selectedAutomation?.id ?? ""}
            onSelect={setSelectedAutomationId}
          />

          <AutomationGroup
            title="Scary situations"
            items={scaryAutomations}
            selectedId={selectedAutomation?.id ?? ""}
            onSelect={setSelectedAutomationId}
          />

          <AutomationGroup
            title="Basic computer help"
            items={basicAutomations}
            selectedId={selectedAutomation?.id ?? ""}
            onSelect={setSelectedAutomationId}
          />

        </article>

        {/* Right Column: Lesson Detail */}
        <article className="w-2/3 flex flex-col overflow-y-auto bg-[#04080f]/60 border border-white/5 rounded-2xl p-8 backdrop-blur-xl shadow-[0_0_30px_rgba(0,0,0,0.5)] custom-scrollbar">
          {selectedAutomation ? (
            <div className="flex flex-col gap-8 pb-10">
              <div className="flex justify-between items-start border-b border-white/10 pb-6">
                <div className="flex flex-col gap-2">
                  <h2 className="text-2xl font-bold text-white">{selectedAutomation.title}</h2>
                  <p className="text-sm text-cyan-300 font-medium">{selectedAutomation.plainEnglish}</p>
                </div>
                <div className="flex gap-2">
                  <span className="px-3 py-1 bg-slate-800/80 border border-white/10 rounded-full text-[10px] uppercase font-bold text-slate-300">{selectedAutomation.group}</span>
                  <span className="px-3 py-1 bg-slate-800/80 border border-white/10 rounded-full text-[10px] uppercase font-bold text-slate-300">{selectedAutomation.category}</span>
                  <span className="px-3 py-1 bg-slate-800/80 border border-white/10 rounded-full text-[10px] uppercase font-bold text-slate-300">{selectedAutomation.difficulty}</span>
                </div>
              </div>

              <LessonSection title="What this means">
                <p className="text-sm text-slate-400 leading-relaxed bg-slate-900/40 p-4 rounded-xl border border-white/5">{selectedAutomation.whatItMeans}</p>
              </LessonSection>

              <LessonSection title="Why this matters">
                <p className="text-sm text-slate-400 leading-relaxed bg-slate-900/40 p-4 rounded-xl border border-white/5">{selectedAutomation.whyThisMatters}</p>
              </LessonSection>

              <LessonSection title="Words to know">
                <div className="flex flex-col gap-3">
                  {selectedAutomation.wordsToKnow.map((item) => (
                    <div key={`${selectedAutomation.id}-${item.term}`} className="bg-slate-900/40 p-4 rounded-xl border border-white/5 flex flex-col gap-1">
                      <span className="text-sm font-bold text-cyan-300">{item.term}</span>
                      <span className="text-sm text-slate-400">{item.meaning}</span>
                    </div>
                  ))}
                </div>
              </LessonSection>

              <div className="grid grid-cols-2 gap-6">
                <LessonCard title="Before you start">
                  <ul className="list-disc list-inside text-sm text-slate-400 flex flex-col gap-1">
                    {selectedAutomation.beforeYouStart.map((item) => (
                      <li key={`${selectedAutomation.id}-before-${item}`}>{item}</li>
                    ))}
                  </ul>
                </LessonCard>

                <LessonCard title="Inputs">
                  <ul className="list-disc list-inside text-sm text-slate-400 flex flex-col gap-1">
                    {selectedAutomation.inputs.map((item) => (
                      <li key={`${selectedAutomation.id}-input-${item}`}>{item}</li>
                    ))}
                  </ul>
                </LessonCard>
              </div>

              <div className="flex flex-col gap-4 bg-slate-900/60 p-6 rounded-2xl border border-cyan-500/20 shadow-[inset_0_0_20px_rgba(34,211,238,0.05)]">
                <h3 className="text-sm font-bold text-cyan-400 uppercase tracking-widest">Execution Steps</h3>
                <ol className="list-decimal list-inside text-sm text-slate-300 flex flex-col gap-3">
                  {selectedAutomation.steps.map((item) => (
                    <li key={`${selectedAutomation.id}-step-${item}`} className="leading-relaxed border-b border-white/5 pb-2 last:border-0">{item}</li>
                  ))}
                </ol>
              </div>

              <div className="grid grid-cols-2 gap-6">
                <LessonCard title="What success looks like">
                  <ul className="list-disc list-inside text-sm text-slate-400 flex flex-col gap-1">
                    {selectedAutomation.whatSuccessLooksLike.map((item) => (
                      <li key={`${selectedAutomation.id}-success-${item}`}>{item}</li>
                    ))}
                  </ul>
                </LessonCard>

                <LessonCard title="When to ask for help">
                  <ul className="list-disc list-inside text-sm text-slate-400 flex flex-col gap-1">
                    {selectedAutomation.whenToAskForHelp.map((item) => (
                      <li key={`${selectedAutomation.id}-help-${item}`}>{item}</li>
                    ))}
                  </ul>
                </LessonCard>
              </div>

              <div className="grid grid-cols-2 gap-6">
                <LessonCard title="Outputs">
                  <ul className="list-disc list-inside text-sm text-slate-400 flex flex-col gap-1">
                    {selectedAutomation.outputs.map((item) => (
                      <li key={`${selectedAutomation.id}-output-${item}`}>{item}</li>
                    ))}
                  </ul>
                </LessonCard>

                <LessonCard title="Common mistakes">
                  <ul className="list-disc list-inside text-sm text-slate-400 flex flex-col gap-1">
                    {selectedAutomation.commonMistakes.map((item) => (
                      <li key={`${selectedAutomation.id}-mistake-${item}`}>{item}</li>
                    ))}
                  </ul>
                </LessonCard>
              </div>

              <LessonSection title="Example Usage">
                <p className="text-sm text-slate-400 leading-relaxed bg-slate-900/40 p-4 rounded-xl border border-white/5 font-mono">{selectedAutomation.example}</p>
              </LessonSection>

              <div className="mt-4 border-t border-white/10 pt-8">
                <AutomationRunner
                  backendUrl={backendUrl}
                  automationId={selectedAutomation.id}
                  automationTitle={selectedAutomation.title}
                  prompt={(selectedAutomation as any).prompt ?? `You are a helpful AI assistant. Complete this task: ${selectedAutomation.plainEnglish}. User input: {input}`}
                  example={selectedAutomation.example}
                  inputs={selectedAutomation.inputs}
                />
              </div>
            </div>
          ) : (
            <div className="flex h-full items-center justify-center text-slate-500 text-sm tracking-widest uppercase">
              Select an automation to start learning
            </div>
          )}
        </article>
      </div>
    </section>
  )
}
