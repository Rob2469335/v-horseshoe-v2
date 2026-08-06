import { useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { api } from "../../lib/api"

export type WorkspaceChanges = {
  stat: { path: string; lines: number }[]
  diff: string
  truncated: boolean
  is_git: boolean
}

function splitPerFile(diff: string): { path: string; hunks: { kind: string; text: string }[] }[] {
  if (!diff) return []
  const blocks = diff.split("\ndiff --git ")
  const out: { path: string; hunks: { kind: string; text: string }[] }[] = []
  for (const block of blocks) {
    const header = block.includes("diff --git ") ? block.split("\ndiff --git ").pop()! : block
    if (!header.trim()) continue
    const m = header.match(/^a\/\S+\s+b\/(\S+)/)
    const path = m ? m[1] : header.split("\n")[0].trim()
    const lines = header.split("\n")
    const hunks: { kind: string; text: string }[] = []
    for (const line of lines.slice(1)) {
      if (line.startsWith("+++") || line.startsWith("---")) {
        hunks.push({ kind: "meta", text: line })
      } else if (line.startsWith("@@")) {
        hunks.push({ kind: "hunk", text: line })
      } else if (line.startsWith("+")) {
        hunks.push({ kind: "add", text: line })
      } else if (line.startsWith("-")) {
        hunks.push({ kind: "del", text: line })
      } else if (line.trim()) {
        hunks.push({ kind: "ctx", text: line })
      }
    }
    out.push({ path, hunks })
  }
  return out
}

const LINE_COLORS: Record<string, string> = {
  add: "text-emerald-400 bg-emerald-400/5",
  del: "text-red-400 bg-red-400/5",
  hunk: "text-cyan-300 bg-cyan-400/5",
  meta: "text-slate-500",
  ctx: "text-slate-300"
}

export default function DiffReviewPanel({ backendUrl }: { backendUrl: string }) {
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const [expanded, setExpanded] = useState(false)

  const { data, isLoading, error } = useQuery<WorkspaceChanges>({
    queryKey: ["workspace-changes", backendUrl],
    queryFn: () => api.getWorkspaceChanges(backendUrl),
    refetchInterval: 15000,
    staleTime: 5000
  })

  const files = useMemo(() => splitPerFile(data?.diff ?? ""), [data])

  const toggle = (path: string) => setOpen((prev) => ({ ...prev, [path]: !prev[path] }))
  const anyChanged = (data?.stat?.length ?? 0) > 0 || files.length > 0

  return (
    <div className="flex flex-col gap-3 bg-[#04080f]/40 border border-cyan-500/20 p-5 rounded-2xl shadow-[inset_0_0_20px_rgba(34,211,238,0.05)]">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-bold text-cyan-400 uppercase tracking-wider">Live Diff Review</h3>
        {data?.is_git && (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="px-2 py-1 text-[10px] uppercase font-bold text-slate-400 border border-white/10 rounded hover:text-cyan-300 hover:border-cyan-500/40 transition-all"
          >
            {expanded ? "Summary" : "Full diff"}
          </button>
        )}
      </div>

      {isLoading && <p className="text-xs text-slate-500">Scanning working tree…</p>}
      {error && <p className="text-xs text-red-400">Could not reach backend: {String(error)}</p>}

      {data && !data.is_git && (
        <p className="text-xs text-slate-500">Not a git work tree — no diff available.</p>
      )}

      {data?.is_git && !anyChanged && !isLoading && (
        <p className="text-xs text-slate-500">Working tree clean — no agent changes to review.</p>
      )}

      {data?.is_git &&
        anyChanged &&
        (expanded ? (
          <div className="flex flex-col gap-2">
            {files.length === 0 && (
              <p className="text-xs text-slate-500">Uncommitted changes detected (use /diff in the CLI for the full view).</p>
            )}
            {files.map((f) => (
              <div key={f.path} className="rounded-lg bg-black/30 border border-white/5 overflow-hidden">
                <div className="px-3 py-2 font-mono text-[11px] text-white/90 bg-white/5 border-b border-white/5 truncate">
                  {f.path}
                </div>
                <div className="px-3 py-2 max-h-56 overflow-auto font-mono text-[11px] leading-relaxed">
                  {f.hunks.map((h, i) => (
                    <div key={i} className={`whitespace-pre-wrap break-all ${LINE_COLORS[h.kind] ?? ""}`}>
                      {h.text}
                    </div>
                  ))}
                </div>
              </div>
            ))}
            {data.truncated && <p className="text-xs text-slate-500">Diff truncated — full view in the CLI via /diff-last.</p>}
          </div>
        ) : (
          <div className="flex flex-col gap-1.5">
            {data.stat.map((f) => (
              <button
                key={f.path}
                type="button"
                onClick={() => toggle(f.path)}
                className="flex items-center justify-between gap-2 px-3 py-2 rounded-lg bg-slate-900/50 border border-white/5 hover:border-cyan-500/40 transition-all text-left"
              >
                <span className="font-mono text-[11px] text-slate-200 truncate">{f.path}</span>
                <span className="flex items-center gap-1.5 shrink-0 text-[10px] font-mono">
                  <span className="text-emerald-400">+{f.lines}</span>
                  <span className="text-slate-500">|</span>
                  <span className={`${open[f.path] ? "text-cyan-300" : "text-slate-500"}`}>{open[f.path] ? "▾" : "▸"}</span>
                </span>
              </button>
            ))}
            {data.stat.length === 0 && files.length > 0 && (
              <p className="text-xs text-slate-500">Changes detected — switch to Full diff to inspect.</p>
            )}
            {data.truncated && <p className="text-xs text-slate-500">Diff truncated — full view in the CLI via /diff-last.</p>}
          </div>
        ))}
    </div>
  )
}
