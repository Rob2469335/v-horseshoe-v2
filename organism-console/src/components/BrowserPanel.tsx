import { useCallback, useEffect, useState } from "react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card"
import { Button } from "./ui/button"
import { Badge } from "./ui/badge"

type A11yNode = { role: string; name: string; value?: string }
type Tab = { title: string; url: string }

type Props = { backendUrl: string }

export default function BrowserPanel({ backendUrl }: Props) {
  const [url, setUrl] = useState("")
  const [tabs, setTabs] = useState<Tab[]>([])
  const [a11y, setA11y] = useState<A11yNode[]>([])
  const [status, setStatus] = useState("")
  const [screenshot, setScreenshot] = useState<string>("")
  const [liveMode, setLiveMode] = useState(false)
  const [visionDesc, setVisionDesc] = useState("")
  const [typeTarget, setTypeTarget] = useState("")
  const [typeText, setTypeText] = useState("")

  const act = useCallback(async (payload: Record<string, unknown>) => {
    try {
      const r = await (await fetch(`${backendUrl}/control/browser/action`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      })).json()
      setStatus(r.ok ? "ok" : `error: ${r.error ?? "unknown"}`)
      if (r.a11y) setA11y(r.a11y)
      return r
    } catch (e: any) {
      setStatus(`error: ${e.message}`)
      return null
    }
  }, [backendUrl])

  const refreshState = useCallback(async () => {
    try {
      const r = await (await fetch(`${backendUrl}/control/browser/state`)).json()
      if (r.ok) setTabs(r.tabs ?? [])
    } catch { /* browser not started yet */ }
  }, [backendUrl])

  useEffect(() => { refreshState() }, [refreshState])

  const navigate = async () => {
    const r = await act({ operation: "navigate", url })
    if (r?.a11y) setA11y(r.a11y)
  }

  const dumpA11y = async () => {
    const r = await act({ operation: "browser_a11y" })
    if (r?.a11y) setA11y(r.a11y)
  }

  const click = async (node: A11yNode) => {
    await act({ operation: "browser_click", name: node.name, role: node.role })
  }

  const doType = async () => {
    if (!typeTarget) return
    await act({ operation: "browser_type", name: typeTarget, text: typeText })
    setTypeText("")
  }

  const shot = async () => {
    const r = await act({ operation: "screenshot" })
    if (r?.ok && r.path) {
      const name = r.path.split(/[\\/]/).pop()
      setScreenshot(`${backendUrl}/control/browser/image?name=${encodeURIComponent(name ?? "")}`)
    }
  }

  // Live view: auto-refresh the screenshot every 4s so you can WATCH the browser
  // (and the agent driving it) — the GhostDesk-style live window.
  useEffect(() => {
    if (!liveMode) return
    const timer = window.setInterval(shot, 4000)
    shot()
    return () => window.clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveMode])

  const describeWithVision = async () => {
    setVisionDesc("")
    const r = await act({ operation: "browser_describe" })
    setVisionDesc(r?.description ?? r?.error ?? "vision unavailable")
  }

  return (
    <Card className="border-white/10 bg-panel">
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <div>
          <CardTitle>Browser</CardTitle>
          <CardDescription>Persistent logged-in browser driven by the accessibility tree (text, not pixels).</CardDescription>
        </div>
        <div className="flex items-center gap-2">
          <Badge className={tabs.length ? "border-emerald-400/40 bg-emerald-400/10 text-emerald-300" : "border-white/20 bg-white/5 text-white/40"}>
            {tabs.length ? `${tabs.length} tab(s)` : "session idle"}
          </Badge>
          <Button size="sm" variant="outline" onClick={refreshState}>Refresh</Button>
          <Button size="sm" variant={liveMode ? "default" : "outline"} onClick={() => setLiveMode(!liveMode)}>
            {liveMode ? "Stop live view" : "Live view"}
          </Button>
          <Button size="sm" variant="outline" onClick={describeWithVision}>Describe (vision)</Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex gap-2">
          <input
            className="flex-1 rounded-md border border-white/10 bg-black/40 px-3 py-1.5 text-sm text-white/80"
            placeholder="https://... navigate"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && navigate()}
          />
          <Button size="sm" onClick={navigate}>Go</Button>
          <Button size="sm" variant="outline" onClick={dumpA11y}>A11y tree</Button>
          <Button size="sm" variant="outline" onClick={shot}>Screenshot</Button>
        </div>
        {status && <div className="text-xs text-amber-300">{status}</div>}

        {tabs.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {tabs.map((t, i) => (
              <Badge key={i} variant="outline" className="truncate max-w-[220px]">{t.title || t.url}</Badge>
            ))}
          </div>
        )}

        <div className="flex gap-2">
          <input className="flex-1 rounded-md border border-white/10 bg-black/40 px-3 py-1.5 text-sm text-white/80" placeholder="Type into element (name)" value={typeTarget} onChange={(e) => setTypeTarget(e.target.value)} />
          <input className="flex-1 rounded-md border border-white/10 bg-black/40 px-3 py-1.5 text-sm text-white/80" placeholder="Text" value={typeText} onChange={(e) => setTypeText(e.target.value)} />
          <Button size="sm" variant="outline" onClick={doType}>Type</Button>
        </div>

        <div className="max-h-64 space-y-1 overflow-y-auto">
          {a11y.length === 0 && <div className="text-sm text-white/40">No a11y tree yet — navigate or dump the tree.</div>}
          {a11y.map((node, i) => (
            <div key={i} className="flex items-center justify-between gap-2 rounded-lg bg-black/20 px-3 py-1.5">
              <div className="min-w-0 truncate">
                <span className="text-xs text-white/30">{node.role}</span>{" "}
                <span className="text-sm">{node.name}</span>
                {node.value != null && <span className="ml-1 text-xs text-white/40">= {node.value}</span>}
              </div>
              {node.role === "button" && (
                <Button size="sm" variant="outline" onClick={() => click(node)}>click</Button>
              )}
            </div>
          ))}
        </div>

        {screenshot && (
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              {liveMode && <Badge className="border-red-400/50 bg-red-400/10 text-red-300">● LIVE</Badge>}
            </div>
            <img src={screenshot} alt="browser" className="w-full rounded-lg border border-white/10" />
          </div>
        )}
        {visionDesc && <pre className="max-h-40 overflow-y-auto whitespace-pre-wrap rounded-lg border border-white/10 bg-black/30 p-3 text-xs text-violet-200">{visionDesc}</pre>}
      </CardContent>
    </Card>
  )
}

