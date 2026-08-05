import { useCallback, useEffect, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { useUiStore } from "../state/ui-store"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/card"
import { Button } from "../components/ui/button"
import { Badge } from "../components/ui/badge"

type LiveEvent = {
  event?: string
  type?: string
  id?: string
  timestamp?: number
  payload?: Record<string, unknown>
}

type ProbeInfo = {
  ok: boolean
  issue: string
  destructive: boolean
  detail: Record<string, unknown>
}

type ControlOverview = {
  available: boolean
  health: {
    health_score: number
    recovery_readiness: number
    active_anomalies: number
    heals_total: number
    heals_success: number
    last_heal_success: boolean | null
    signals: Array<Record<string, unknown>>
  }
  probes: Record<string, ProbeInfo>
  screen: {
    available: boolean
    autonomous: boolean
    action_count: number
    max_actions: number
    foreground_window: string
    cursor: Record<string, unknown>
    windows: Array<Record<string, unknown>>
    error?: string
  }
  models: {
    installed_models: string[]
    agent_models: Record<string, { model: string; backend: string }>
  }
  memory_counts: Record<string, number>
  resilience: {
    models_in_cooldown: Array<{ model: string; failures: number; cooldown_remaining_s: number; last_error: string }>
    fallback_stats: Record<string, number>
    error?: string
  }
}

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { Accept: "application/json", ...(init?.body ? { "Content-Type": "application/json" } : {}) },
    ...init,
  })
  if (!res.ok) throw new Error(`HTTP ${res.status} ${res.statusText}`)
  return (await res.json()) as T
}

function ProbeBadge({ ok, destructive }: { ok: boolean; destructive: boolean }) {
  if (ok) return <Badge className="border-emerald-400/40 bg-emerald-400/10 text-emerald-300">healthy</Badge>
  if (destructive) return <Badge className="border-amber-400/40 bg-amber-400/10 text-amber-300">needs approval</Badge>
  return <Badge variant="destructive">issue</Badge>
}

function probeTitle(issue: string) {
  return issue.replace(/_/g, " ")
}

function eventTone(ev: LiveEvent): "ok" | "err" | "warn" {
  const name = `${ev.event ?? ev.type ?? ""} ${JSON.stringify(ev.payload ?? {})}`.toLowerCase()
  if (name.includes("error") || name.includes("fail")) return "err"
  if (name.includes("warn") || name.includes("approval") || name.includes("timeout")) return "warn"
  if (name.includes("complete") || name.includes("success") || name.includes("ok")) return "ok"
  return "warn"
}

function eventSummary(ev: LiveEvent): string {
  const payload = ev.payload ?? {}
  const parts: string[] = []
  if (typeof payload.agent_id === "string" && payload.agent_id) parts.push(payload.agent_id)
  if (typeof payload.action === "string" && payload.action) parts.push(payload.action)
  if (typeof payload.model === "string" && payload.model) parts.push(payload.model)
  if (typeof payload.status === "string" && payload.status) parts.push(payload.status)
  const rest = Object.entries(payload)
    .filter(([k, v]) => !["agent_id", "action", "model", "status", "type"].includes(k) && typeof v === "string")
    .map(([k, v]) => `${k}=${v}`)
    .slice(0, 2)
  return [...parts, ...rest].join(" · ") || JSON.stringify(payload).slice(0, 80)
}

function probeSummary(info: ProbeInfo): string {
  const d = info.detail ?? {}
  const parts: string[] = []
  if (typeof d.ram_percent === "number") parts.push(`RAM ${d.ram_percent}%`)
  if (typeof d.temp_gb === "number") parts.push(`${d.temp_gb} GB temp`)
  if (typeof d.errors === "number") parts.push(`${d.errors} errors`)
  if (Array.isArray(d.drives) && d.drives.length) {
    parts.push(d.drives.map((x: Record<string, unknown>) => `${x.device} ${x.percent}%`).join(", "))
  }
  if (Array.isArray(d.processes) && d.processes.length) {
    parts.push(d.processes.map((p: Record<string, unknown>) => `${p.name} (${p.cpu_percent}%)`).join(", "))
  }
  return parts.join(" · ") || (info.ok ? "all clear" : "check detail")
}

export default function CommandCenterPage() {
  const backendUrl = useUiStore((state) => state.backendUrl)
  const [approvalPending, setApprovalPending] = useState<Record<string, boolean>>({})
  const [actionLog, setActionLog] = useState<Array<{ t: string; msg: string; tone: "ok" | "err" | "warn" }>>([])
  const [screenUrl, setScreenUrl] = useState<string>("")
  const [screenStatus, setScreenStatus] = useState<string>("")
  const [agentPick, setAgentPick] = useState<Record<string, string>>({})
  const [liveFeed, setLiveFeed] = useState<LiveEvent[]>([])
  const [watchMode, setWatchMode] = useState(false)
  const watchTimerRef = useRef<number | null>(null)

  const logRef = useRef<HTMLDivElement>(null)
  const log = useCallback((msg: string, tone: "ok" | "err" | "warn" = "ok") => {
    setActionLog((prev) => [{ t: new Date().toLocaleTimeString(), msg, tone }, ...prev].slice(0, 40))
  }, [])
  useEffect(() => {
    logRef.current?.scrollTo({ top: 0 })
  }, [actionLog])

  const overviewQuery = useQuery({
    queryKey: ["control-overview", backendUrl],
    queryFn: () => fetchJson<ControlOverview>(`${backendUrl}/control/overview`),
    retry: 1,
    refetchInterval: 10000,
  })
  const data = overviewQuery.data

  // Live execution trace feed (SSE) — timestamped swarm events with status pills.
  useEffect(() => {
    if (!backendUrl) return
    const es = new EventSource(`${backendUrl}/swarm/v10/stream`)
    es.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data) as LiveEvent
        if (ev?.type === "ping" && !ev?.event) return
        setLiveFeed((prev) => [{ ...ev, timestamp: ev.timestamp ?? Date.now() / 1000 }, ...prev].slice(0, 60))
      } catch {
        /* ignore malformed frames */
      }
    }
    es.onerror = () => es.close()
    return () => es.close()
  }, [backendUrl])

  // Screen watch mode — re-capture the live view every 4s so you can watch the
  // swarm drive the machine (GhostDesk-style live view).
  useEffect(() => {
    if (watchMode) {
      const timer = window.setInterval(() => takeScreenshot(), 4000)
      watchTimerRef.current = timer
      return () => window.clearInterval(timer)
    }
  }, [watchMode])

  // Initialize agent pick selections from the model mapping once loaded.
  useEffect(() => {
    if (data?.models?.agent_models) {
      setAgentPick((prev) => {
        const next = { ...prev }
        for (const [agent, m] of Object.entries(data.models.agent_models)) {
          if (!next[agent]) next[agent] = m.model
        }
        return next
      })
    }
  }, [data?.models?.agent_models])

  const runAction = useCallback(
    async (label: string, path: string, body?: unknown, onData?: (d: any) => void) => {
      try {
        const d = await fetchJson<any>(`${backendUrl}${path}`, body !== undefined ? { method: "POST", body: JSON.stringify(body) } : undefined)
        log(label, (d?.result?.ok ?? d?.status === "ok") ? "ok" : "warn")
        onData?.(d)
        return d
      } catch (err: any) {
        log(`${label}: ${err.message ?? err}`, "err")
        return null
      }
    },
    [backendUrl, log]
  )

  const recoverIssue = (issue: string, info: ProbeInfo) => {
    if (info.destructive && !approvalPending[issue]) {
      setApprovalPending((p) => ({ ...p, [issue]: true }))
      log(`Approval required for ${issue} — click again to confirm`, "warn")
      return
    }
    setApprovalPending((p) => ({ ...p, [issue]: false }))
    runAction(`recover ${issue}`, "/control/recover", { issue, approved: true })
  }

  const takeScreenshot = async () => {
    const d = await runAction("screenshot", "/control/screen/action", { action: "screenshot", kwargs: {} })
    const path: string | undefined = d?.result?.result?.path
    if (path) {
      const name = path.split(/[\\/]/).pop()
      setScreenUrl(`${backendUrl}/control/screen/image?name=${encodeURIComponent(name ?? "")}`)
      setScreenStatus(d.result.result.foreground_window ? `fg: ${d.result.result.foreground_window}` : "captured")
    } else if (d?.result?.ok === false) {
      setScreenStatus(d.result.error ?? "screenshot failed")
    }
  }

  const screenInput = (action: string, kwargs: Record<string, unknown> = {}) => {
    runAction(`screen ${action}`, "/control/screen/action", { action, kwargs })
  }

  const reassignModel = (agent: string, model: string) => {
    runAction(`assign ${model} → ${agent}`, `/control/agents/${agent}/model`, { model_name: model, backend: "local" })
  }

  const probes = data?.probes ?? {}
  const screen = data?.screen
  const models = data?.models
  const installedModels = models?.installed_models ?? []
  const agentModels = models?.agent_models ?? {}
  const health = data?.health
  const memoryCounts = data?.memory_counts ?? {}
  const cooled = data?.resilience?.models_in_cooldown ?? []
  const cooledCount = cooled.length

  const statusTone =
    !health ? "bg-slate-800/60" :
    health.health_score >= 80 ? "bg-emerald-500/15 border-emerald-400/30" :
    health.health_score >= 50 ? "bg-amber-500/15 border-amber-400/30" : "bg-red-500/15 border-red-400/30"

  return (
    <section className="page">
      <div className="pageheader">
        <div>
          <h1 className="flex items-center gap-3">
            Command Center
            {overviewQuery.isError ? (
              <Badge variant="destructive">backend unreachable</Badge>
            ) : (
              <ProbeBadge ok={(health?.health_score ?? 0) >= 80} destructive={false} />
            )}
          </h1>
          <p className="page-subtitle">
            See and control the whole machine: system probes, healing, screen, models, and agent routing — one surface.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            disabled={!health?.health_score}
            onClick={async () => {
              const d = await runAction("run heal cycle", "/control/heal/run", { force: true })
              if (d?.heal_cycle != null) log(`heal cycle: ${d.heal_cycle}`, d.heal_cycle === "run" ? "ok" : "warn")
              overviewQuery.refetch()
            }}
          >
            Run heal cycle
          </Button>
          <Button onClick={() => overviewQuery.refetch()} variant="outline">Refresh</Button>
        </div>
      </div>

      {actionLog.length > 0 && (
        <div ref={logRef} className="mb-6 max-h-40 overflow-y-auto rounded-xl border border-white/10 bg-black/30 p-3 font-mono text-xs space-y-1">
          {actionLog.map((entry, i) => (
            <div key={i} className={
              entry.tone === "ok" ? "text-emerald-300" :
              entry.tone === "warn" ? "text-amber-300" : "text-red-300"
            }>
              <span className="text-white/30">[{entry.t}]</span> {entry.msg}
            </div>
          ))}
        </div>
      )}

      {/* ============ Status strip ============ */}
      <div className={`mb-6 grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3 rounded-2xl border p-4 ${statusTone}`}>
        <Stat label="Health score" value={health?.health_score?.toFixed(0) ?? "—"} sub={health ? `${health.recovery_readiness} readiness` : undefined} />
        <Stat label="Active anomalies" value={String(health?.active_anomalies ?? "—")} sub={health?.active_anomalies ? "watch" : "clear"} />
        <Stat label="Heals total" value={String(health?.heals_total ?? "—")} sub={health?.heals_success != null ? `${health.heals_success} success` : undefined} />
        <Stat label="Last heal" value={health?.last_heal_success == null ? "—" : health.last_heal_success ? "success" : "failed"} />
        <Stat label="Screen mode" value={screen?.autonomous ? "autonomous" : "human-control"} sub={screen?.action_count != null ? `${screen.action_count}/${screen.max_actions} actions` : undefined} />
        <Stat label="Models online" value={String(installedModels.length)} sub={installedModels[0] ?? undefined} />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
        {/* ============ System probes ============ */}
        <Card className="border-white/10 bg-panel">
          <CardHeader>
            <CardTitle>System probes</CardTitle>
            <CardDescription>
              Whole-computer health — safe issues auto-heal, destructive ops (kill/clean/restart) require your approval.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {Object.entries(probes).length === 0 && (
              <div className="text-sm text-white/40">No probe data yet{overviewQuery.isLoading ? "…" : ""}</div>
            )}
            {Object.entries(probes).map(([name, info]) => (
              <div key={name} className="flex flex-col gap-2 rounded-xl border border-white/10 bg-black/20 p-3">
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="font-semibold capitalize truncate">{probeTitle(info.issue)}</span>
                    <ProbeBadge ok={info.ok} destructive={info.destructive} />
                    {info.destructive && !info.ok && (
                      <Badge className="border-fuchsia-400/40 bg-fuchsia-400/10 text-fuchsia-300">destructive</Badge>
                    )}
                  </div>
                  {!info.ok && (
                    <Button
                      size="sm"
                      variant={info.destructive ? (approvalPending[name] ? "destructive" : "outline") : "default"}
                      onClick={() => recoverIssue(name, info)}
                    >
                      {approvalPending[name] ? "Confirm recovery" : info.destructive ? "Request recovery" : "Recover"}
                    </Button>
                  )}
                </div>
                <div className="text-xs text-white/50">{probeSummary(info)}</div>
              </div>
            ))}
          </CardContent>
        </Card>

        {/* ============ Screen control ============ */}
        <Card className="border-white/10 bg-panel">
          <CardHeader>
            <CardTitle>Screen control</CardTitle>
            <CardDescription>
              Computer-use tier — read-only by default. Flip to autonomous to let the swarm drive the mouse &amp; keyboard.
              Risky input (typing secrets, system shortcuts) still requires approval even in autonomous mode.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant={screen?.autonomous ? "destructive" : "default"}
                onClick={() => runAction("toggle autonomous", "/control/screen/autonomous", { enabled: !screen?.autonomous })}
              >
                {screen?.autonomous ? "Disable autonomous" : "Enable autonomous"}
              </Button>
              <Button variant="outline" onClick={takeScreenshot}>Capture screenshot</Button>
              <Button
                variant={watchMode ? "default" : "outline"}
                onClick={() => {
                  if (watchMode) {
                    setWatchMode(false)
                    if (watchTimerRef.current) window.clearInterval(watchTimerRef.current)
                  } else {
                    setWatchMode(true)
                    takeScreenshot()
                  }
                }}
              >
                {watchMode ? "Stop watching" : "Watch live"}
              </Button>
              <Button
                variant="outline"
                onClick={() => runAction("reset action count", "/control/screen/reset")}
                disabled={!screen?.action_count}
              >
                Reset action count
              </Button>
              <Badge className={screen?.autonomous ? "border-red-400/50 bg-red-400/10 text-red-300" : "border-emerald-400/40 bg-emerald-400/10 text-emerald-300"}>
                {screen?.autonomous ? "AUTONOMOUS — real input" : "human-control"}
              </Badge>
            </div>

            <div className="grid grid-cols-2 gap-3 text-xs">
              <InfoRow label="Foreground window" value={screen?.foreground_window || "—"} />
              <InfoRow
                label="Cursor"
                value={screen?.cursor?.x != null ? `${screen.cursor.x}, ${screen.cursor.y}` : "—"}
              />
            </div>

            {screenStatus && <div className="text-xs text-amber-300">{screenStatus}</div>}
            {screenUrl && (
              <a href={screenUrl} target="_blank" rel="noreferrer">
                <img src={screenUrl} alt="screen" className="w-full rounded-lg border border-white/10" />
              </a>
            )}

            <div>
              <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-white/40">Input actions (gated)</div>
              <div className="flex flex-wrap gap-2">
                <Button size="sm" variant="outline" onClick={() => screenInput("mouse_move", { x: 960, y: 540 })}>Move center</Button>
                <Button size="sm" variant="outline" onClick={() => screenInput("left_click")}>Left click</Button>
                <Button size="sm" variant="outline" onClick={() => screenInput("right_click")}>Right click</Button>
                <Button size="sm" variant="outline" onClick={() => screenInput("double_click")}>Double click</Button>
                <Button size="sm" variant="outline" onClick={() => screenInput("scroll", { direction: "down", amount: 3 })}>Scroll down</Button>
                <Button size="sm" variant="outline" onClick={() => screenInput("key", { name: "enter" })}>Enter</Button>
              </div>
              {!screen?.autonomous && (
                <div className="mt-2 text-xs text-white/40">
                  Input is blocked in human-control mode — enable autonomous first, or the swarm can only see and propose.
                </div>
              )}
            </div>

            {screen?.windows && screen.windows.length > 0 && (
              <div>
                <div className="mb-1 text-xs font-semibold uppercase tracking-wider text-white/40">Open windows</div>
                <div className="max-h-32 overflow-y-auto space-y-1 text-xs">
                  {screen.windows.map((w: any, i) => (
                    <div key={i} className="truncate rounded bg-black/20 px-2 py-1 text-white/50">{w.title}</div>
                  ))}
                </div>
              </div>
            )}
          </CardContent>
        </Card>

        {/* ============ Models & agents ============ */}
        <Card className="border-white/10 bg-panel">
          <CardHeader>
            <CardTitle>Models &amp; agent routing</CardTitle>
            <CardDescription>Installed models and per-agent assignment. Pick a model to reassign an agent live.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap gap-2">
              {installedModels.length === 0 && <span className="text-sm text-white/40">No models detected.</span>}
              {installedModels.map((m) => (
                <Badge key={m} variant={m === installedModels[0] ? "default" : "outline"}>{m}</Badge>
              ))}
            </div>
            <div className="space-y-2">
              {Object.entries(agentModels).map(([agent, m]) => (
                <div key={agent} className="flex items-center justify-between gap-3 rounded-xl border border-white/10 bg-black/20 p-2">
                  <div className="min-w-0">
                    <div className="font-semibold capitalize truncate">{agent}</div>
                    <div className="text-xs text-white/40">{m.backend}</div>
                  </div>
                  <div className="flex items-center gap-2">
                    <select
                      className="rounded-md border border-white/10 bg-black/40 px-2 py-1 text-xs text-white/80"
                      value={agentPick[agent] ?? m.model}
                      onChange={(e) => {
                        setAgentPick((p) => ({ ...p, [agent]: e.target.value }))
                        reassignModel(agent, e.target.value)
                      }}
                    >
                      {installedModels.map((model) => (
                        <option key={model} value={model}>{model}</option>
                      ))}
                    </select>
                  </div>
                </div>
              ))}
              {Object.keys(agentModels).length === 0 && (
                <div className="text-sm text-white/40">No agent model mapping loaded.</div>
              )}
            </div>
          </CardContent>
        </Card>

        {/* ============ Memory ============ */}
        <Card className="border-white/10 bg-panel">
          <CardHeader>
            <CardTitle>Memory stores</CardTitle>
            <CardDescription>Live point counts per Qdrant collection.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {Object.entries(memoryCounts).length === 0 && <div className="text-sm text-white/40">No memory collections.</div>}
            {Object.entries(memoryCounts).map(([name, count]) => (
              <div key={name} className="flex items-center justify-between rounded-xl border border-white/10 bg-black/20 px-3 py-2">
                <span className="text-sm capitalize">{name.replace(/_/g, " ")}</span>
                <Badge variant="outline">{count}</Badge>
              </div>
            ))}
          </CardContent>
        </Card>

        {/* ============ Resilience / gateway ============ */}
        <Card className="border-white/10 bg-panel">
          <CardHeader>
            <CardTitle>Resilience &amp; gateway</CardTitle>
            <CardDescription>
              Models in cooldown after failures (exponential backoff + jitter) and the fallback pool
              by provider. Cooldowns skip a failing model before the next LLM call.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div>
              <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-white/40">Fallback pool</div>
              <div className="flex flex-wrap gap-2">
                {Object.entries(data?.resilience?.fallback_stats ?? {}).length === 0 && (
                  <span className="text-sm text-white/40">No fallback stats loaded.</span>
                )}
                {Object.entries(data?.resilience?.fallback_stats ?? {}).map(([provider, count]) => (
                  <Badge key={provider} variant="outline" className="capitalize">
                    {provider.replace("_", " ")}: {count}
                  </Badge>
                ))}
              </div>
            </div>
            <div>
              <div className="mb-2 flex items-center justify-between">
                <div className="text-xs font-semibold uppercase tracking-wider text-white/40">Models in cooldown</div>
                <Badge className={cooledCount ? "border-amber-400/40 bg-amber-400/10 text-amber-300" : "border-emerald-400/40 bg-emerald-400/10 text-emerald-300"}>
                  {cooledCount ? `${cooledCount} cooling` : "all clear"}
                </Badge>
              </div>
              {cooledCount === 0 && <div className="text-sm text-white/40">No models in cooldown — fallbacks are serving.</div>}
              <div className="space-y-2">
                {cooled.map((c) => (
                  <div key={c.model} className="rounded-xl border border-amber-400/20 bg-amber-400/5 px-3 py-2">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono text-xs font-semibold text-amber-200 truncate">{c.model}</span>
                      <span className="whitespace-nowrap text-xs text-white/50">
                        {c.cooldown_remaining_s}s · {c.failures} failures
                      </span>
                    </div>
                    {c.last_error && <div className="mt-1 truncate text-[11px] text-white/40" title={c.last_error}>{c.last_error}</div>}
                  </div>
                ))}
              </div>
            </div>
          </CardContent>
        </Card>

        {/* ============ Live execution trace feed ============ */}
        <Card className="border-white/10 bg-panel xl:col-span-2">
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <div>
              <CardTitle>Live execution trace</CardTitle>
              <CardDescription>Timestamped swarm events from the event bus — watch agents work in real time.</CardDescription>
            </div>
            <Badge className={liveFeed.length ? "border-emerald-400/40 bg-emerald-400/10 text-emerald-300" : "border-white/20 bg-white/5 text-white/40"}>
              {liveFeed.length ? "streaming" : "idle"}
            </Badge>
          </CardHeader>
          <CardContent>
            {liveFeed.length === 0 && (
              <div className="rounded-xl border border-dashed border-white/15 p-6 text-center text-sm text-white/40">
                No events yet — run an agent or trigger a heal cycle to see live traces.
              </div>
            )}
            {liveFeed.length > 0 && (
              <div className="max-h-72 space-y-1 overflow-y-auto font-mono text-xs">
                {liveFeed.map((ev, i) => {
                  const tone = eventTone(ev)
                  const ts = ev.timestamp ? new Date(ev.timestamp * 1000).toLocaleTimeString() : ""
                  return (
                    <div key={i} className="flex items-start gap-2 rounded-lg bg-black/20 px-2 py-1">
                      <span className="text-white/30 whitespace-nowrap">{ts}</span>
                      <Badge
                        className={
                          tone === "err"
                            ? "border-red-400/40 bg-red-400/10 text-red-300"
                            : tone === "ok"
                              ? "border-emerald-400/40 bg-emerald-400/10 text-emerald-300"
                              : "border-amber-400/40 bg-amber-400/10 text-amber-300"
                        }
                      >
                        {ev.event ?? ev.type ?? "event"}
                      </Badge>
                      <span className={tone === "err" ? "text-red-200" : "text-white/70"}>{eventSummary(ev)}</span>
                    </div>
                  )
                })}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </section>
  )
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-xl bg-black/20 px-3 py-2">
      <div className="text-[10px] uppercase tracking-wider text-white/40">{label}</div>
      <div className="text-lg font-bold leading-tight">{value}</div>
      {sub && <div className="text-[11px] text-white/40 truncate">{sub}</div>}
    </div>
  )
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-black/20 px-3 py-2">
      <div className="text-[10px] uppercase tracking-wider text-white/40">{label}</div>
      <div className="text-sm truncate">{value}</div>
    </div>
  )
}
