import React, { useEffect, useMemo, useState } from "react"
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
  AdminDashboardResponse,
  AdminGenerationResponse,
  AdminStatusResponse,
  HealthResponse,
  ReadyResponse,
  StatusResponse,
  ToolsResponse,
  TraceItem,
  TraceSummaryItem,
  TraceSummaryResponse,
  TracesResponse
} from "../lib/types"
import { useUiStore } from "../state/ui-store"
import { appConfig } from "../lib/config"

function formatJson(value: unknown) {
  return JSON.stringify(value, null, 2)
}

function formatTimestamp(timestampMs?: number) {
  if (!timestampMs) return "—"
  return new Date(timestampMs).toLocaleString()
}

function formatDuration(durationMs?: number) {
  if (!durationMs) return "0 ms"
  if (durationMs < 1000) return `${durationMs.toFixed(1)} ms`
  return `${(durationMs / 1000).toFixed(2)} s`
}

function DisclosurePanel({
  title,
  children,
  defaultOpen = false
}: {
  title: string
  children: React.ReactNode
  defaultOpen?: boolean
}) {
  return (
    <details className="ops-panel disclosure-panel" open={defaultOpen}>
      <summary className="disclosure-panel__summary">
        <span>{title}</span>
        <span className="disclosure-panel__hint">{defaultOpen ? "Open by default" : "Click to expand"}</span>
      </summary>
      <div className="disclosure-panel__content">{children}</div>
    </details>
  )
}

export default function OpsPage() {
  const backendUrl = useUiStore((state) => state.backendUrl)
  const setConnectionStatus = useUiStore((state) => state.setConnectionStatus)

  const [selectedTraceId, setSelectedTraceId] = useState<string>("")
  const [selectedAutomationId, setSelectedAutomationId] = useState<string>(starterAutomations[0]?.id ?? "")

  const healthQuery = useQuery({
    queryKey: ["health", backendUrl],
    queryFn: () => api.getHealth<HealthResponse>(backendUrl),
    retry: 1,
    refetchInterval: 30000
  })

  const readyQuery = useQuery({
    queryKey: ["readyz", backendUrl],
    queryFn: () => api.getReady<ReadyResponse>(backendUrl),
    retry: 1,
    refetchInterval: 30000
  })

  const statusQuery = useQuery({
    queryKey: ["status", backendUrl],
    queryFn: () => api.getStatus<StatusResponse>(backendUrl),
    retry: 1,
    refetchInterval: 30000
  })

  const toolsQuery = useQuery({
    queryKey: ["tools", backendUrl],
    queryFn: () => api.getTools<ToolsResponse>(backendUrl),
    retry: 1,
    refetchInterval: 60000
  })

  const tracesQuery = useQuery({
    queryKey: ["traces", backendUrl],
    queryFn: () => api.getTraces(backendUrl),
    retry: 1,
    refetchInterval: 15000
  })

  const traceSummaryQuery = useQuery({
    queryKey: ["trace-summary", backendUrl],
    queryFn: () => api.getTraceSummary(backendUrl),
    retry: 1,
    refetchInterval: 15000
  })

  const adminStatusQuery = useQuery({
    queryKey: ["admin-status", backendUrl],
    queryFn: () => api.getAdminStatus<AdminStatusResponse>(backendUrl),
    retry: 1,
    refetchInterval: 60000
  })

  const adminDashboardQuery = useQuery({
    queryKey: ["admin-dashboard", backendUrl],
    queryFn: () => api.getAdminDashboard<AdminDashboardResponse>(backendUrl),
    retry: 1,
    refetchInterval: 60000
  })

  const adminGenerationQuery = useQuery({
    queryKey: ["admin-generation", backendUrl],
    queryFn: () => api.getAdminGeneration<AdminGenerationResponse>(backendUrl),
    retry: 1,
    refetchInterval: 60000
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

  const traceSummary = (traceSummaryQuery.data ?? []) as TraceSummaryResponse
  const traceItems = ((tracesQuery.data as TracesResponse | undefined)?.items ?? []) as TraceItem[]

  useEffect(() => {
    if (!traceSummary.length) {
      setSelectedTraceId("")
      return
    }

    setSelectedTraceId((current) => {
      if (current && traceSummary.some((item) => item.trace_id === current)) {
        return current
      }

      return traceSummary[0]?.trace_id ?? ""
    })
  }, [traceSummary])

  const selectedSummary = useMemo<TraceSummaryItem | undefined>(() => {
    return traceSummary.find((item) => item.trace_id === selectedTraceId)
  }, [traceSummary, selectedTraceId])

  const selectedTraceEvents = useMemo<TraceItem[]>(() => {
    return traceItems.filter((item) => item.trace_id === selectedTraceId)
  }, [traceItems, selectedTraceId])

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
            </>
          ) : (
            <div className="trace-empty">Select an automation to start learning.</div>
          )}
        </article>
      </div>

      <div className="ops-grid">
        <DisclosurePanel title={appConfig.endpoints.health}>
          <pre>{healthQuery.data ? formatJson(healthQuery.data) : String(healthQuery.error?.message ?? "Loading")}</pre>
        </DisclosurePanel>

        <DisclosurePanel title={appConfig.endpoints.ready}>
          <pre>{readyQuery.data ? formatJson(readyQuery.data) : String(readyQuery.error?.message ?? "Loading")}</pre>
        </DisclosurePanel>

        <DisclosurePanel title={appConfig.endpoints.status}>
          <pre>{statusQuery.data ? formatJson(statusQuery.data) : String(statusQuery.error?.message ?? "Loading")}</pre>
        </DisclosurePanel>

        <DisclosurePanel title={appConfig.endpoints.tools}>
          <pre>{toolsQuery.data ? formatJson(toolsQuery.data) : String(toolsQuery.error?.message ?? "Loading")}</pre>
        </DisclosurePanel>

        <article className="ops-panel ops-panel--span-2">
          <h2>{appConfig.endpoints.traceSummary}</h2>
          {traceSummaryQuery.data ? (
            <div className="trace-summary-list">
              {traceSummary.length ? (
                traceSummary.map((item) => (
                  <button
                    key={item.trace_id}
                    type="button"
                    className={`trace-summary-item${item.trace_id === selectedTraceId ? " trace-summary-item--active" : ""}`}
                    onClick={() => setSelectedTraceId(item.trace_id)}
                  >
                    <div className="trace-summary-item__top">
                      <span className="trace-summary-item__trace">{item.trace_id}</span>
                      <span className="trace-summary-item__time">{formatTimestamp(item.latest_timestamp_ms)}</span>
                    </div>
                    <div className="trace-summary-item__meta">
                      <span>{item.first_phase} → {item.last_status}</span>
                      <span>{item.action_count} steps</span>
                      <span>{formatDuration(item.total_duration_ms)}</span>
                    </div>
                    <div className="trace-summary-item__summary">{item.summary || "No summary"}</div>
                  </button>
                ))
              ) : (
                <div className="trace-empty">No trace summaries available.</div>
              )}
            </div>
          ) : (
            <pre>{String(traceSummaryQuery.error?.message ?? "Loading")}</pre>
          )}
        </article>

        <article className="ops-panel ops-panel--span-2">
          <h2>{appConfig.endpoints.traces}</h2>
          {tracesQuery.data ? (
            <div className="trace-events">
              {selectedSummary ? (
                <div className="trace-events__header">
                  <div><strong>Selected trace:</strong> {selectedSummary.trace_id}</div>
                  <div><strong>Latest:</strong> {formatTimestamp(selectedSummary.latest_timestamp_ms)}</div>
                </div>
              ) : null}

              {selectedTraceEvents.length ? (
                <div className="trace-event-list">
                  {selectedTraceEvents.map((event, index) => (
                    <article key={`${event.trace_id}-${event.step_id}-${event.phase}-${index}`} className="trace-event-card">
                      <div className="trace-event-card__top">
                        <span className="trace-event-card__phase">{event.phase}</span>
                        <span className="trace-event-card__status">{event.status}</span>
                      </div>
                      <div className="trace-event-card__meta">
                        <span><strong>Actor:</strong> {event.actor}</span>
                        <span><strong>Action:</strong> {event.action}</span>
                        <span><strong>Step:</strong> {event.step_id}</span>
                        <span><strong>Time:</strong> {formatTimestamp(event.timestamp_ms)}</span>
                        <span><strong>Duration:</strong> {formatDuration(event.duration_ms)}</span>
                        <span><strong>Model:</strong> {event.model || "—"}</span>
                      </div>
                      <div className="trace-event-card__summary">{event.summary || "No summary"}</div>
                      {event.metadata ? <pre>{formatJson(event.metadata)}</pre> : null}
                    </article>
                  ))}
                </div>
              ) : (
                <div className="trace-empty">No raw events available for the selected trace.</div>
              )}
            </div>
          ) : (
            <pre>{String(tracesQuery.error?.message ?? "Loading")}</pre>
          )}
        </article>

        <DisclosurePanel title={appConfig.endpoints.adminStatus}>
          <pre>{adminStatusQuery.data ? formatJson(adminStatusQuery.data) : String(adminStatusQuery.error?.message ?? "Loading")}</pre>
        </DisclosurePanel>

        <DisclosurePanel title={appConfig.endpoints.adminDashboard}>
          <pre>{adminDashboardQuery.data ? formatJson(adminDashboardQuery.data) : String(adminDashboardQuery.error?.message ?? "Loading")}</pre>
        </DisclosurePanel>

        <DisclosurePanel title={appConfig.endpoints.adminGeneration}>
          <pre>{adminGenerationQuery.data ? formatJson(adminGenerationQuery.data) : String(adminGenerationQuery.error?.message ?? "Loading")}</pre>
        </DisclosurePanel>
      </div>
    </section>
  )
}


