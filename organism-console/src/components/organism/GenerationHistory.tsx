import { useState, useEffect } from "react"
interface TraceItem { trace_id:string; step_id:string; phase:string; actor:string; action:string; status:string; duration_ms?:number; timestamp_ms?:number; model?:string; summary?:string; metadata?:Record<string,unknown> }
interface Props { backendUrl: string }
export function GenerationHistory({ backendUrl }: Props) {
  const [traces, setTraces] = useState<TraceItem[]>([])
  const [search, setSearch] = useState("")
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState<string|null>(null)
  async function load() {
    setLoading(true)
    try { const r = await fetch(`${backendUrl}/traces?limit=100`); const d = await r.json(); setTraces(d.traces??[]) }
    catch { setTraces([]) } finally { setLoading(false) }
  }
  useEffect(()=>{load()},[backendUrl])
  const filtered = traces.filter(t=>{ const q=search.toLowerCase(); return !q||t.model?.toLowerCase().includes(q)||t.phase?.toLowerCase().includes(q)||t.status?.toLowerCase().includes(q)||t.summary?.toLowerCase().includes(q) })
  const grouped = filtered.reduce<Record<string,TraceItem[]>>((acc,item)=>{ if(!acc[item.trace_id])acc[item.trace_id]=[]; acc[item.trace_id].push(item); return acc },{})
  function sc(s:string){ if(s.includes("success")||s.includes("accepted")||s.includes("completed")||s.includes("selected"))return"#86efac"; if(s.includes("fail")||s.includes("rejected")||s.includes("error"))return"#f87171"; return"#fbbf24" }
  return (
    <div style={{ background:"rgba(255,255,255,0.03)", border:"1px solid rgba(255,255,255,0.08)", borderRadius:20, padding:"18px 20px" }}>
      <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:14 }}>
        <div style={{ fontSize:11, fontWeight:700, color:"rgba(255,255,255,0.45)", textTransform:"uppercase", letterSpacing:"0.1em" }}>📜 Generation history</div>
        <div style={{ display:"flex", gap:8, alignItems:"center" }}>
          <input value={search} onChange={e=>setSearch(e.target.value)} placeholder="Search model, phase, status..." style={{ padding:"6px 12px", borderRadius:10, border:"1px solid rgba(255,255,255,0.1)", background:"rgba(0,0,0,0.3)", color:"white", fontSize:12, outline:"none", width:220 }} />
          <button onClick={load} style={{ padding:"6px 12px", borderRadius:10, border:"1px solid rgba(255,255,255,0.1)", background:"transparent", color:"rgba(255,255,255,0.6)", fontSize:12, cursor:"pointer" }}>{loading?"⏳":"🔄"} Refresh</button>
        </div>
      </div>
      {!Object.keys(grouped).length ? <div style={{ textAlign:"center", padding:"32px 0", color:"rgba(255,255,255,0.3)", fontSize:14 }}>{loading?"Loading...":"No generations yet. Run an agent step to see history here."}</div> : (
        <div style={{ display:"grid", gap:8, maxHeight:420, overflowY:"auto" }}>
          {Object.entries(grouped).map(([tid,items])=>{
            const critic=items.find(i=>i.phase==="critic"); const score=critic?.metadata?.score as number|undefined
            const model=items.find(i=>i.model)?.model??"—"
            const gen=items.find(i=>i.phase==="generator"); const ms=gen?.duration_ms
            const dur=ms?(ms<1000?`${Math.round(ms)}ms`:`${(ms/1000).toFixed(1)}s`):"—"
            const last=items[items.length-1]?.status??"unknown"
            const isOpen=selected===tid
            const ts=items[0]?.timestamp_ms?new Date(items[0].timestamp_ms).toLocaleTimeString():"—"
            return (
              <div key={tid} onClick={()=>setSelected(isOpen?null:tid)} style={{ background:"rgba(0,0,0,0.25)", border:`1px solid ${isOpen?"rgba(125,211,252,0.3)":"rgba(255,255,255,0.06)"}`, borderRadius:14, padding:"12px 14px", cursor:"pointer" }}>
                <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center" }}>
                  <div style={{ display:"flex", gap:10, alignItems:"center" }}>
                    <span style={{ width:8, height:8, borderRadius:"50%", background:sc(last), display:"inline-block" }} />
                    <span style={{ fontSize:12, color:"rgba(255,255,255,0.5)", fontFamily:"monospace" }}>{tid.slice(0,12)}…</span>
                    <span style={{ fontSize:12, color:"rgba(255,255,255,0.4)" }}>{model}</span>
                  </div>
                  <div style={{ display:"flex", gap:12, alignItems:"center" }}>
                    {score!==undefined&&<span style={{ fontSize:12, color:score>=0.8?"#86efac":score>=0.6?"#fbbf24":"#f87171", fontWeight:700 }}>Score: {score.toFixed(2)}</span>}
                    <span style={{ fontSize:11, color:"rgba(255,255,255,0.3)" }}>{dur}</span>
                    <span style={{ fontSize:11, color:"rgba(255,255,255,0.3)" }}>{ts}</span>
                  </div>
                </div>
                {isOpen&&<div style={{ marginTop:12, paddingTop:12, borderTop:"1px solid rgba(255,255,255,0.06)", display:"grid", gap:6 }}>
                  {items.map((item,i)=>(
                    <div key={i} style={{ display:"flex", gap:10, alignItems:"flex-start", fontSize:12 }}>
                      <span style={{ color:sc(item.status), flexShrink:0, width:70 }}>{item.phase}</span>
                      <span style={{ color:"rgba(255,255,255,0.5)", flexShrink:0 }}>{item.action}</span>
                      <span style={{ color:sc(item.status), flexShrink:0 }}>{item.status}</span>
                      {item.summary&&<span style={{ color:"rgba(255,255,255,0.4)" }}>{item.summary}</span>}
                    </div>
                  ))}
                </div>}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
