import { useState } from "react"
interface Props { backendUrl: string; selectedModel: string|null }
export function AgentStepRunner({ backendUrl, selectedModel }: Props) {
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<{status:string;model?:string;content?:string;message?:string;duration_ms?:number}|null>(null)
  const [prompt, setPrompt] = useState("")
  async function run() {
    const p = prompt.trim() || "Run a system heartbeat check and summarize current organism status in plain English."
    setRunning(true); setResult(null)
    const start = Date.now()
    try {
      const res = await fetch(`${backendUrl}/generate`, { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({prompt:p,model:selectedModel}) })
      const data = await res.json()
      setResult({ status:"success", model:data.model, content:data.content, duration_ms:Date.now()-start })
    } catch(e) { setResult({ status:"error", message:String(e), duration_ms:Date.now()-start }) }
    finally { setRunning(false) }
  }
  return (
    <div style={{ background:"rgba(255,255,255,0.03)", border:"1px solid rgba(255,255,255,0.08)", borderRadius:20, padding:"18px 20px" }}>
      <div style={{ fontSize:11, fontWeight:700, color:"rgba(255,255,255,0.45)", textTransform:"uppercase", letterSpacing:"0.1em", marginBottom:12 }}>⚡ Agent step runner</div>
      <textarea value={prompt} onChange={e=>setPrompt(e.target.value)} placeholder="Type a task or leave blank for a heartbeat check..." rows={2}
        style={{ width:"100%", background:"rgba(0,0,0,0.3)", border:"1px solid rgba(255,255,255,0.1)", borderRadius:12, padding:"10px 14px", color:"white", fontSize:13, resize:"vertical", outline:"none", fontFamily:"inherit", lineHeight:1.6, marginBottom:10, boxSizing:"border-box" }} />
      <div style={{ display:"flex", gap:10, alignItems:"center" }}>
        <button onClick={run} disabled={running} style={{ padding:"9px 20px", borderRadius:12, border:"none", background:running?"rgba(255,255,255,0.1)":"linear-gradient(135deg,#378ADD,#7dd3fc)", color:"white", fontWeight:700, fontSize:13, cursor:running?"not-allowed":"pointer" }}>
          {running?"⏳ Running...":"▶ Run agent step"}
        </button>
        {selectedModel && <span style={{ fontSize:12, color:"rgba(255,255,255,0.45)" }}>using {selectedModel}</span>}
      </div>
      {result && (
        <div style={{ marginTop:14, padding:"14px 16px", borderRadius:14, background:result.status==="success"?"rgba(34,197,94,0.06)":"rgba(239,68,68,0.06)", border:`1px solid ${result.status==="success"?"rgba(34,197,94,0.2)":"rgba(239,68,68,0.2)"}` }}>
          {result.status==="success" ? (
            <>
              <div style={{ display:"flex", justifyContent:"space-between", marginBottom:8 }}>
                <span style={{ fontSize:11, color:"#22c55e", fontWeight:700, textTransform:"uppercase" }}>✅ Completed</span>
                <span style={{ fontSize:11, color:"rgba(255,255,255,0.4)" }}>{result.model} · {((result.duration_ms??0)/1000).toFixed(1)}s</span>
              </div>
              <div style={{ fontSize:13, color:"rgba(230,238,255,0.85)", lineHeight:1.75, whiteSpace:"pre-wrap" }}>{result.content}</div>
            </>
          ) : <div style={{ fontSize:13, color:"#f87171" }}>❌ {result.message}</div>}
        </div>
      )}
    </div>
  )
}
