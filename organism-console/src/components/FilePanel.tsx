import { useCallback, useState } from "react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card"
import { Button } from "./ui/button"
import { Badge } from "./ui/badge"

type SearchResult = { path?: string; name?: string; content?: string; score?: number }
type Props = { backendUrl: string }

export default function FilePanel({ backendUrl }: Props) {
  const [query, setQuery] = useState("")
  const [results, setResults] = useState<SearchResult[]>([])
  const [currentPath, setCurrentPath] = useState("")
  const [fileContent, setFileContent] = useState("")
  const [question, setQuestion] = useState("")
  const [answer, setAnswer] = useState("")
  const [answerLoading, setAnswerLoading] = useState(false)
  const [editMode, setEditMode] = useState(false)
  const [editContent, setEditContent] = useState("")
  const [editStatus, setEditStatus] = useState("")
  const [pendingWrite, setPendingWrite] = useState(false)
  const [busy, setBusy] = useState(false)

  const search = async () => {
    if (!query.trim()) return
    setBusy(true)
    try {
      const r = await (await fetch(`${backendUrl}/features/search`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query, top_k: 5 }),
      })).json()
      setResults(r.results ?? [])
    } catch (e: any) {
      setResults([])
    } finally { setBusy(false) }
  }

  const readFile = async (path: string) => {
    setCurrentPath(path); setEditMode(false); setEditStatus("")
    try {
      const r = await (await fetch(`${backendUrl}/control/file/read?path=${encodeURIComponent(path)}`)).json()
      setFileContent(r.content ?? r.error ?? "")
      setEditContent(r.content ?? "")
      if (!r.ok) setEditStatus(r.error ?? "")
    } catch (e: any) {
      setFileContent(`Error reading ${path}: ${e.message}`)
    }
  }

  const askLocalModel = async () => {
    if (!question.trim() || !currentPath) return
    setAnswerLoading(true); setAnswer("")
    const context = fileContent.slice(0, 12000)
    try {
      const r = await (await fetch(`${backendUrl}/generate`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: `You are a senior engineer helping the user understand a local file.\n\nFILE: ${currentPath}\n\n${context}\n\nUSER QUESTION: ${question}\n\nAnswer using ONLY the file content above. Be precise and cite specific lines/functions.` }),
      })).json()
      setAnswer(r.content ?? "No answer returned.")
    } catch (e: any) {
      setAnswer(`Error: ${e.message}`)
    } finally { setAnswerLoading(false) }
  }

  const stageWrite = async () => {
    if (!currentPath) return
    setEditStatus("Write staged — click 'Confirm write' to apply (approval required).")
    setPendingWrite(true)
  }

  const confirmWrite = async () => {
    if (!currentPath) return
    try {
      const r = await (await fetch(`${backendUrl}/control/file/write`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: currentPath, content: editContent, approved: true }),
      })).json()
      setEditStatus(r.ok ? "✓ saved" : `✗ ${r.error ?? "write failed"}`)
      if (r.ok) { setFileContent(editContent); setEditMode(false); setPendingWrite(false) }
    } catch (e: any) {
      setEditStatus(`✗ ${e.message}`)
    }
  }

  return (
    <Card className="border-white/10 bg-panel">
      <CardHeader>
        <CardTitle>Files & Local Q&A</CardTitle>
        <CardDescription>
          Search your codebase, read any file, ask the local model questions about it, and edit with approval — all local, no cloud.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex gap-2">
          <input
            className="flex-1 rounded-md border border-white/10 bg-black/40 px-3 py-1.5 text-sm text-white/80"
            placeholder="Search codebase — e.g. 'agent routing logic'"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && search()}
          />
          <Button size="sm" disabled={busy} onClick={search}>{busy ? "…" : "Search"}</Button>
        </div>

        {results.length > 0 && (
          <div className="max-h-40 space-y-1 overflow-y-auto">
            {results.map((r, i) => (
              <button
                key={i}
                onClick={() => r.path && readFile(r.path)}
                className="flex w-full items-center justify-between gap-2 rounded-lg bg-black/20 px-3 py-2 text-left hover:bg-black/40"
              >
                <div className="min-w-0">
                  <div className="truncate text-sm font-mono">{r.path ?? r.name}</div>
                  <div className="truncate text-xs text-white/40">{r.content?.slice(0, 80)}</div>
                </div>
                {r.score != null && <Badge variant="outline">{r.score.toFixed(2)}</Badge>}
              </button>
            ))}
          </div>
        )}

        {currentPath && (
          <>
            <div className="flex items-center justify-between gap-2">
              <div className="min-w-0 truncate font-mono text-sm">{currentPath}</div>
              <div className="flex gap-2">
                <Button size="sm" variant="outline" onClick={() => setEditMode(!editMode)}>
                  {editMode ? "Cancel" : "Edit"}
                </Button>
              </div>
            </div>

            {editMode ? (
              <div className="space-y-2">
                <textarea
                  className="h-64 w-full rounded-md border border-white/10 bg-black/40 px-3 py-2 font-mono text-xs text-white/80"
                  value={editContent}
                  onChange={(e) => setEditContent(e.target.value)}
                />
                <div className="flex items-center gap-2">
                  {!pendingWrite ? (
                    <Button size="sm" onClick={stageWrite}>Stage write (approval)</Button>
                  ) : (
                    <Button size="sm" variant="destructive" onClick={confirmWrite}>Confirm write</Button>
                  )}
                  {editStatus && <span className="text-xs text-amber-300">{editStatus}</span>}
                </div>
              </div>
            ) : (
              <pre className="h-64 overflow-auto rounded-md border border-white/10 bg-black/30 p-3 font-mono text-[11px] text-white/70 whitespace-pre-wrap">
                {fileContent || "(empty)"}
              </pre>
            )}

            <div className="rounded-xl border border-white/10 bg-black/20 p-3 space-y-2">
              <div className="text-xs font-semibold uppercase tracking-wider text-white/40">Ask the local model about this file</div>
              <input
                className="w-full rounded-md border border-white/10 bg-black/40 px-3 py-1.5 text-sm text-white/80"
                placeholder="e.g. 'what does this function do?'"
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && askLocalModel()}
              />
              <Button size="sm" disabled={answerLoading} onClick={askLocalModel}>
                {answerLoading ? "Thinking…" : "Ask local model"}
              </Button>
              {answer && <pre className="max-h-48 overflow-y-auto whitespace-pre-wrap text-xs text-emerald-200">{answer}</pre>}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}
