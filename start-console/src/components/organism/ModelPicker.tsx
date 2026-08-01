import { useState } from "react"
interface Props { models: string[]; selected: string|null; onSelect: (m:string|null)=>void }
function getColor(m:string) {
  if (m.includes("vl")||m.includes("moondream")) return "#D4537E"
  if (m.includes("coder")) return "#378ADD"
  if (m.includes("embed")||m.includes("reranker")) return "#94a3b8"
  if (m.includes("14b")||m.includes("12b")) return "#a78bfa"
  if (m.includes("3b")) return "#94a3b8"
  return "#7dd3fc"
}
function getRole(m:string) {
  if (m.includes("vl")||m.includes("moondream")) return "Vision"
  if (m.includes("coder")) return "Coding"
  if (m.includes("embed")) return "Embedding"
  if (m.includes("reranker")) return "Reranker"
  if (m.includes("14b")||m.includes("12b")) return "Reasoning"
  if (m.includes("3b")) return "Fast"
  return "Balanced"
}
export function ModelPicker({ models, selected, onSelect }: Props) {
  const [open, setOpen] = useState(false)
  return (
    <div style={{ position:"relative" }}>
      <button onClick={()=>setOpen(!open)} style={{ display:"flex", alignItems:"center", gap:8, padding:"8px 14px", borderRadius:12, border:"1px solid rgba(255,255,255,0.15)", background:selected?"rgba(125,211,252,0.08)":"rgba(255,255,255,0.04)", color:"white", cursor:"pointer", fontSize:13 }}>
        🧠 {selected??"Auto-select model"} ▾
      </button>
      {open && (
        <div style={{ position:"absolute", top:"110%", left:0, zIndex:50, background:"#0a1628", border:"1px solid rgba(255,255,255,0.12)", borderRadius:16, padding:8, minWidth:280, boxShadow:"0 20px 60px rgba(0,0,0,0.5)", maxHeight:320, overflowY:"auto" }}>
          <div onClick={()=>{onSelect(null);setOpen(false)}} style={{ padding:"8px 12px", borderRadius:10, cursor:"pointer", fontSize:13, color:"rgba(255,255,255,0.6)", marginBottom:4 }}>✨ Auto-select (recommended)</div>
          {models.map(m => (
            <div key={m} onClick={()=>{onSelect(m);setOpen(false)}} style={{ display:"flex", justifyContent:"space-between", alignItems:"center", padding:"8px 12px", borderRadius:10, cursor:"pointer", background:selected===m?"rgba(125,211,252,0.1)":"transparent" }}>
              <span style={{ fontSize:13, color:"white" }}>{m}</span>
              <span style={{ fontSize:11, padding:"2px 8px", borderRadius:999, background:getColor(m)+"22", color:getColor(m), fontWeight:600 }}>{getRole(m)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
