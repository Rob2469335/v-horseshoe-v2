import { useCallback, useEffect, useState } from "react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card"
import { Button } from "./ui/button"
import { Badge } from "./ui/badge"

type Task = {
  id: string
  goal: string
  schedule: string
  enabled: boolean
  last_run?: string | null
  result?: Record<string, unknown> | null
}

type Props = { backendUrl: string }

export default function TasksPanel({ backendUrl }: Props) {
  const [tasks, setTasks] = useState<Task[]>([])
  const [goal, setGoal] = useState("")
  const [schedule, setSchedule] = useState("daily 08:00")
  const [status, setStatus] = useState("")

  const refresh = useCallback(async () => {
    try {
      const r = await (await fetch(`${backendUrl}/control/tasks`)).json()
      setTasks(r.tasks ?? [])
    } catch { /* backend not ready */ }
  }, [backendUrl])

  useEffect(() => { refresh() }, [refresh])

  const create = async () => {
    if (!goal.trim()) return
    const r = await (await fetch(`${backendUrl}/control/tasks`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ goal, schedule, enabled: true }),
    })).json()
    setStatus(r.ok ? `Created task ${r.task?.id}` : `Failed: ${JSON.stringify(r)}`)
    setGoal("")
    refresh()
  }

  const toggle = async (t: Task) => {
    await fetch(`${backendUrl}/control/tasks/${t.id}/toggle`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: !t.enabled }),
    })
    refresh()
  }

  const remove = async (t: Task) => {
    await fetch(`${backendUrl}/control/tasks/${t.id}`, { method: "DELETE" })
    refresh()
  }

  const runNow = async () => {
    const r = await (await fetch(`${backendUrl}/control/tasks/run`, { method: "POST" })).json()
    setStatus(r.ok ? `Ran ${r.ran?.length ?? 0} due task(s)` : "No due tasks")
    refresh()
  }

  const resultText = (t: Task) => {
    const r = t.result
    if (!r) return "never run"
    if (r.blocked) return `blocked: ${r.blocked}${r.reason ? ` (${r.reason})` : ""}`
    if (r.ok) return `ok: ${JSON.stringify(r).slice(0, 60)}`
    return `failed: ${String(r.error ?? "").slice(0, 50)}`
  }

  return (
    <Card className="border-white/10 bg-panel xl:col-span-2">
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <div>
          <CardTitle>Recurring Tasks</CardTitle>
          <CardDescription>Scheduled agent work. Safety: tasks can only reach read/search actions; send/purchase/login goals are hard-blocked, and unmapped goals are refused (fail-closed).</CardDescription>
        </div>
        <Button size="sm" variant="outline" onClick={runNow}>Run due now</Button>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex gap-2">
          <input className="flex-1 rounded-md border border-white/10 bg-black/40 px-3 py-1.5 text-sm text-white/80" placeholder="e.g. summarize my email inbox" value={goal} onChange={(e) => setGoal(e.target.value)} onKeyDown={(e) => e.key === "Enter" && create()} />
          <select className="rounded-md border border-white/10 bg-black/40 px-2 py-1.5 text-sm text-white/80" value={schedule} onChange={(e) => setSchedule(e.target.value)}>
            <option value="daily 08:00">daily 08:00</option>
            <option value="daily 17:00">daily 17:00</option>
            <option value="hourly">hourly</option>
          </select>
          <Button size="sm" onClick={create}>Add</Button>
        </div>
        {status && <div className="text-xs text-amber-300">{status}</div>}

        {tasks.length === 0 && <div className="text-sm text-white/40">No scheduled tasks yet.</div>}
        <div className="space-y-2">
          {tasks.map((t) => (
            <div key={t.id} className="flex items-center justify-between gap-3 rounded-lg bg-black/20 px-3 py-2">
              <div className="min-w-0">
                <div className="truncate text-sm font-semibold">{t.goal}</div>
                <div className="truncate text-xs text-white/40">
                  {t.schedule} · {resultText(t)}
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <Badge className={t.enabled ? "border-emerald-400/40 bg-emerald-400/10 text-emerald-300" : "border-white/20 bg-white/5 text-white/40"}>
                  {t.enabled ? "on" : "off"}
                </Badge>
                <Button size="sm" variant="outline" onClick={() => toggle(t)}>{t.enabled ? "Pause" : "Resume"}</Button>
                <Button size="sm" variant="destructive" onClick={() => remove(t)}>×</Button>
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}
