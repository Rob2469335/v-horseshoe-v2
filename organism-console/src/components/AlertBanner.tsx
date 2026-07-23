import { useEffect, useState } from "react"
import { useUiStore } from "../state/ui-store"
import { motion, AnimatePresence } from "framer-motion"

interface Alert { id:string; level:"warn"|"error"|"info"; message:string; detail:string }
const C = { 
  warn:  { bg:"bg-amber-500/10", border:"border-amber-500/30", text:"text-amber-400", dot:"bg-amber-500" }, 
  error: { bg:"bg-red-500/10", border:"border-red-500/30", text:"text-red-400", dot:"bg-red-500" }, 
  info:  { bg:"bg-blue-500/10", border:"border-blue-500/30", text:"text-blue-400", dot:"bg-blue-500" } 
}

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
        try { 
          const hr=await fetch(`${backendUrl}/api/admin/healing/evaluate`); 
          const heal=await hr.json(); 
          if((heal.active_anomalies??0)>0) next.push({id:"anomalies",level:"warn",message:`${heal.active_anomalies} active anomalies`,detail:"Check the Organism page self-healing control."}) 
        } catch (err) { console.error("Error checking healing:", err); }
        setAlerts(next.filter(a=>!dismissed.has(a.id)))
      } catch (err) {
        console.error("Backend status check failed:", err);
        setAlerts([{id:"backend-down", level:"error", message:"Backend Offline", detail:"Cannot connect to Swarm OS API."}]);
      }
    }
    check(); const t=setInterval(check,30000); return()=>clearInterval(t)
  },[backendUrl,dismissed])

  const visible=alerts.filter(a=>!dismissed.has(a.id))
  if(!visible.length) return null

  return (
    <div className="fixed top-4 right-4 z-[1000] flex flex-col gap-2 max-w-[380px]">
      <AnimatePresence>
        {visible.map(a=>{ 
          const c=C[a.level]; 
          return (
            <motion.div 
              key={a.id} 
              layout
              initial={{ opacity: 0, y: -20, scale: 0.95 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, scale: 0.95, filter: "blur(10px)" }}
              transition={{ type: "spring", stiffness: 400, damping: 25 }}
              className={`flex items-start gap-2.5 p-3.5 rounded-2xl border backdrop-blur-xl shadow-2xl ${c.bg} ${c.border}`}
            >
              <span className={`w-2 h-2 rounded-full shrink-0 mt-1.5 shadow-[0_0_12px_currentColor] ${c.dot}`} />
              <div className="flex-1">
                <div className={`text-[13px] font-black mb-0.5 ${c.text}`}>{a.message}</div>
                <div className="text-[12px] text-white/60 leading-relaxed font-medium">{a.detail}</div>
              </div>
              <button 
                onClick={()=>setDismissed(p=>new Set([...p,a.id]))} 
                className="text-white/40 hover:text-white/80 transition-colors cursor-pointer text-base p-1 leading-none -mt-1 -mr-1"
                aria-label="Dismiss alert"
              >
                ×
              </button>
            </motion.div>
          )
        })}
      </AnimatePresence>
    </div>
  )
}
