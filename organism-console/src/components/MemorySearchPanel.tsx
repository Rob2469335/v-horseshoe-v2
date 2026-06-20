import { useState } from "react"
import { useUiStore } from "../state/ui-store"
interface MemoryResult { id:string; score:number; text:string; source:string; timestamp:string }
export function MemorySearchPanel() {
  const backendUrl=useUiStore(s=>s.backendUrl)
  const [query,setQuery]=useState(""); const [results,setResults]=useState<MemoryResult[]>([]); const [loading,setLoading]=useState(false); const [error,setError]=useState<string|null>(null); const [searched,setSearched]=useState(false)
  async function search() {
    if(!query.trim()) return; setLoading(true); setError(null)
    try { const res=await fetch(`${backendUrl}/memory/search?q=${encodeURIComponent(query)}&limit=8`); const data=await res.json(); if(data.error)setError(data.error); setResults(data.results??[]); setSearched(true) }
    catch(e){setError(String(e));setResults([])} finally{setLoading(false)}
  }
  function sc(s:number){return s>=0.8?"#86efac":s>=0.6?"#fbbf24":"#f87171"}
  return (
    <div style={{ background:"rgba(255,255,255,0.03)", border:"1px solid rgba(255,255,255,0.08)", borderRadius:20, padding:"18px 20px" }}>
      <div style={{ fontSize:11, fontWeight:700, color:"rgba(255,255,255,0.45)", textTransform:"uppercase", letterSpacing:"0.1em", marginBottom:12 }}>🧬 Semantic memory search</div>
      <div style={{ fontSize:13, color:"rgba(200,220,255,0.6)", marginBottom:14, lineHeight:1.6 }}>Search your AI long-term memory stored in Qdrant.</div>
      <div style={{ display:"flex", gap:8, marginBottom:16 }}>
        <input value={query} onChange={e=>setQuery(e.target.value)} onKeyDown={e=>e.key==="Enter"&&search()} placeholder="e.g. fitness optimization, coding task..." style={{ flex:1, padding:"10px 14px", borderRadius:12, border:"1px solid rgba(255,255,255,0.12)", background:"rgba(0,0,0,0.3)", color:"white", fontSize:13, outline:"none" }} />
        <button onClick={search} disabled={loading||!query.trim()} style={{ padding:"10px 18px", borderRadius:12, border:"none", background:loading?"rgba(255,255,255,0.1)":"linear-gradient(135deg,#378ADD,#7dd3fc)", color:"white", fontWeight:700, fontSize:13, cursor:loading?"not-allowed":"pointer" }}>{loading?"⏳":"🔍"} Search</button>
      </div>
      {error&&<div style={{ padding:"10px 14px", borderRadius:12, background:"rgba(239,68,68,0.08)", border:"1px solid rgba(239,68,68,0.2)", fontSize:13, color:"#fca5a5", marginBottom:12 }}>⚠️ {error}</div>}
      {searched&&!results.length&&!error&&<div style={{ textAlign:"center", padding:"24px 0", color:"rgba(255,255,255,0.3)", fontSize:14 }}>No memories found. Run more agent tasks to build memory.</div>}
      {results.length>0&&<div style={{ display:"grid", gap:8 }}>{results.map((r,i)=>(
        <div key={r.id??i} style={{ background:"rgba(0,0,0,0.25)", border:"1px solid rgba(255,255,255,0.06)", borderRadius:14, padding:"12px 14px" }}>
          <div style={{ display:"flex", justifyContent:"space-between", marginBottom:6 }}>
            <span style={{ fontSize:11, color:"rgba(255,255,255,0.4)", textTransform:"uppercase" }}>{r.source}</span>
            <span style={{ fontSize:12, fontWeight:700, color:sc(r.score) }}>Match: {Math.round(r.score*100)}%</span>
          </div>
          <div style={{ fontSize:13, color:"rgba(220,235,255,0.85)", lineHeight:1.7 }}>{r.text||"(no text)"}</div>
          {r.timestamp&&<div style={{ fontSize:11, color:"rgba(255,255,255,0.25)", marginTop:6 }}>{r.timestamp}</div>}
        </div>
      ))}</div>}
    </div>
  )
}
