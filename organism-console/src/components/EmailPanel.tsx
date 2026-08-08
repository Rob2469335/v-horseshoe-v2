import { useCallback, useEffect, useRef, useState } from "react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card"
import { Button } from "./ui/button"
import { Badge } from "./ui/badge"

type EmailMessage = {
  id: string
  subject: string
  from: string
  to: string
  date: string
  attachments: number
  body?: string
}

type Props = { backendUrl: string }

export default function EmailPanel({ backendUrl }: Props) {
  const [status, setStatus] = useState<{ configured: boolean; reason?: string } | null>(null)
  const [inbox, setInbox] = useState<EmailMessage[]>([])
  const [query, setQuery] = useState("")
  const [selected, setSelected] = useState<EmailMessage | null>(null)
  const [draft, setDraft] = useState({ to: "", subject: "", body: "" })
  const [draftResult, setDraftResult] = useState<string>("")
  const [sending, setSending] = useState(false)
  const pendingTokenRef = useRef<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const s = await (await fetch(`${backendUrl}/control/email/status`)).json()
      setStatus(s)
      if (s.configured) {
        const r = await (await fetch(`${backendUrl}/control/email/list`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ folder: "INBOX", limit: 20 }),
        })).json()
        setInbox(r.messages ?? [])
      }
    } catch {
      setStatus({ configured: false, reason: "backend unreachable" })
    }
  }, [backendUrl])

  useEffect(() => { refresh() }, [refresh])

  const search = async () => {
    if (!query.trim()) { refresh(); return }
    const r = await (await fetch(`${backendUrl}/control/email/search`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, limit: 20 }),
    })).json()
    setInbox(r.messages ?? [])
  }

  const openMsg = async (m: EmailMessage) => {
    const r = await (await fetch(`${backendUrl}/control/email/read`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ uid: m.id }),
    })).json()
    setSelected({ ...m, body: r.body })
  }

  const stageDraft = async () => {
    const r = await (await fetch(`${backendUrl}/control/email/draft`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(draft),
    })).json()
    if (r.ok && r.send_token) {
      setDraftResult(`Draft staged — approval required before send. Token expires in ${r.expires_in_s}s.`)
      pendingTokenRef.current = r.send_token
    } else {
      setDraftResult(`Draft failed: ${r.error ?? "unknown"}`)
    }
  }

  const confirmSend = async () => {
    const token = pendingTokenRef.current
    if (!token) { setDraftResult("No pending draft to send."); return }
    setSending(true)
    const r = await (await fetch(`${backendUrl}/control/email/send`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ send_token: token, confirmed: true }),
    })).json()
    setSending(false)
    setDraftResult(r.ok ? `✓ Sent to ${r.to}: ${r.subject}` : `Send failed: ${r.error}`)
    pendingTokenRef.current = null
  }

  return (
    <Card className="border-white/10 bg-panel">
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <div>
          <CardTitle>Email</CardTitle>
          <CardDescription>Inbox as a tool — read free, send requires approval.</CardDescription>
        </div>
        <div className="flex items-center gap-2">
          <Badge className={status?.configured ? "border-emerald-400/40 bg-emerald-400/10 text-emerald-300" : "border-amber-400/40 bg-amber-400/10 text-amber-300"}>
            {status?.configured ? "connected" : "not configured"}
          </Badge>
          <Button size="sm" variant="outline" onClick={refresh}>Refresh</Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {status && !status.configured && (
          <div className="rounded-xl border border-amber-400/20 bg-amber-400/5 p-3 text-sm text-amber-200">
            {status.reason ?? "Email not configured."} Create config/email_config.json (see config/email_config.example.json).
          </div>
        )}

        <div className="flex gap-2">
          <input
            className="flex-1 rounded-md border border-white/10 bg-black/40 px-3 py-1.5 text-sm text-white/80"
            placeholder="Search subject/from..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && search()}
          />
          <Button size="sm" variant="outline" onClick={search}>Search</Button>
        </div>

        {inbox.length === 0 && <div className="text-sm text-white/40">No messages. Configure email to see your inbox.</div>}
        <div className="max-h-64 space-y-1 overflow-y-auto">
          {inbox.map((m) => (
            <button
              key={m.id}
              onClick={() => openMsg(m)}
              className="flex w-full items-center justify-between gap-3 rounded-lg bg-black/20 px-3 py-2 text-left hover:bg-black/40"
            >
              <div className="min-w-0">
                <div className="truncate text-sm font-semibold">{m.subject || "(no subject)"}</div>
                <div className="truncate text-xs text-white/40">{m.from}</div>
              </div>
              <div className="flex shrink-0 items-center gap-2 text-xs text-white/40">
                {m.attachments > 0 && <span>📎{m.attachments}</span>}
                <span className="whitespace-nowrap">{m.date?.slice(5, 16)}</span>
              </div>
            </button>
          ))}
        </div>

        {selected && (
          <div className="rounded-xl border border-white/10 bg-black/20 p-3">
            <div className="mb-1 font-semibold">{selected.subject}</div>
            <div className="mb-2 text-xs text-white/40">From: {selected.from} · {selected.date}</div>
            <pre className="max-h-48 overflow-y-auto whitespace-pre-wrap text-xs text-white/70">{selected.body}</pre>
          </div>
        )}

        <div className="rounded-xl border border-white/10 bg-black/20 p-3 space-y-2">
          <div className="text-xs font-semibold uppercase tracking-wider text-white/40">Compose (send is human-approved)</div>
          <input className="w-full rounded-md border border-white/10 bg-black/40 px-3 py-1.5 text-sm text-white/80" placeholder="To" value={draft.to} onChange={(e) => setDraft({ ...draft, to: e.target.value })} />
          <input className="w-full rounded-md border border-white/10 bg-black/40 px-3 py-1.5 text-sm text-white/80" placeholder="Subject" value={draft.subject} onChange={(e) => setDraft({ ...draft, subject: e.target.value })} />
          <textarea className="w-full rounded-md border border-white/10 bg-black/40 px-3 py-1.5 text-sm text-white/80" rows={4} placeholder="Body" value={draft.body} onChange={(e) => setDraft({ ...draft, body: e.target.value })} />
          <div className="flex gap-2">
            <Button size="sm" onClick={stageDraft}>Stage draft (approval)</Button>
            <Button size="sm" variant="destructive" disabled={sending} onClick={confirmSend}>Confirm & send</Button>
          </div>
          {draftResult && <div className="text-xs text-amber-300">{draftResult}</div>}
        </div>
      </CardContent>
    </Card>
  )
}

