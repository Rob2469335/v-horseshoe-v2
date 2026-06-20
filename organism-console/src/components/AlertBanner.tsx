import { useEffect, useState } from "react"
import { useUiStore } from "../state/ui-store"
interface Alert { id:string; level:"warn"|"error"|"info"; message:string; detail:string }
const C = { warn:{bg:"rgba(180,120,0,0.12)",border:"rgba(250,190,0,0.3)",text:"#fbbf24",dot:"#f59e0b"}, error:{bg:"rgba(180,40,40,0.12)",border:"rgba(240,80,80,0.3)",text:"#f87171",dot:"#ef4444"}, info:{bg:"rgba(40,100,200,0.10)",border:"rgba(100,160,255,0.25)",text:"#93c5fd",dot:"#60a5fa"} }
export function AlertBanner() {
  const backendUrl = useUiStore(s=>s.backendUrl)
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [dismissed, setDismissed] = useState<Set<string>>(new Set())
  useEffect(()=>{
    async function check() {
      try {
        const res=await fetch(`${backendUrl}/status`); const data=await res.json(); const next:Alert[]=[]
        if(!data.ready) next.push({id:"not-ready",level:"warn",message:"System not fully ready",detail:"Some services are still starting up."})
        if(!data.ollama_reachable) next.push({id:"ollama-down",level:"error",message:"AI brain is offline",detail:"Run 'ollama serve' in a terminal to fix this."})
        if(data.installed_model_count===0) next.push({id:"no-models",level:"warn",message:"No AI models loaded",detail:"Run 'ollama pull qwen2.5:3b-instruct' to load a model."})
        try { const hr=await fetch(`${backendUrl}/healing/evaluate`); const heal=await hr.json(); if((heal.active_anomalies??0)>0) next.push({id:"anomalies",level:"warn",message:`${heal.active_anomalies} active anomalies`,detail:"Check the Organism page self-healing control."}) } catch {}
        setAlerts(next.filter(a=>!dismissed.has(a.id)))
      } catch {}
    }
    check(); const t=setInterval(check,30000); return()=>clearInterval(t)
  },[backendUrl,dismissed])
  const visible=alerts.filter(a=>!dismissed.has(a.id))
  if(!visible.length) return null
  return (
    <div style={{ position:"fixed", top:12, right:12, zIndex:1000, display:"grid", gap:8, maxWidth:380 }}>
      {visible.map(a=>{ const c=C[a.level]; return (
        <div key={a.id} style={{ background:c.bg, border:`1px solid ${c.border}`, borderRadius:14, padding:"12px 14px", display:"flex", gap:10, alignItems:"flex-start", boxShadow:"0 8px 32px rgba(0,0,0,0.3)" }}>
          <span style={{ width:8, height:8, borderRadius:"50%", background:c.dot, flexShrink:0, marginTop:4 }} />
          <div style={{ flex:1 }}>
            <div style={{ fontSize:13, fontWeight:700, color:c.text, marginBottom:3 }}>{a.message}</div>
            <div style={{ fontSize:12, color:"rgba(255,255,255,0.55)", lineHeight:1.6 }}>{a.detail}</div>
          </div>
          <button onClick={()=>setDismissed(p=>new Set([...p,a.id]))} style={{ background:"none", border:"none", color:"rgba(255,255,255,0.3)", cursor:"pointer", fontSize:16, padding:0 }}>×</button>
        </div>
      )})}
    </div>
  )
}
