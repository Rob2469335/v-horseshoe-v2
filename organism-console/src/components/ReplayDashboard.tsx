import { useQuery } from "@tanstack/react-query"
import { useUiStore } from "../state/ui-store"
import { motion } from "framer-motion"

interface ReplayData { event_count:number; latest_health_score:number|null; healing_attempts:number; last_action:string|null; components:Record<string,unknown> }
interface EventsData { count:number; events:{event_id:string;event_type:string;occurred_at:string;source:string;payload:Record<string,unknown>}[] }

export function ReplayDashboard() {
  const backendUrl = useUiStore(s=>s.backendUrl)
  const rq = useQuery<ReplayData>({queryKey:["replay",backendUrl],queryFn:async()=>(await fetch(`${backendUrl}/api/admin/replay`)).json(),refetchInterval:30000})
  const eq = useQuery<EventsData>({queryKey:["events-recent",backendUrl],queryFn:async()=>(await fetch(`${backendUrl}/events?limit=20`)).json(),refetchInterval:15000})
  
  const replay = rq.data
  const events = eq.data?.events ?? []

  function ec(t:string | undefined | null){
    if(!t) return "bg-slate-400 text-slate-400"
    if(t.includes("evolve")) return "bg-purple-400 text-purple-400"
    if(t.includes("llm")||t.includes("generate")) return "bg-sky-300 text-sky-300"
    if(t.includes("agent")) return "bg-green-300 text-green-300"
    if(t.includes("heal")) return "bg-pink-400 text-pink-400"
    return "bg-slate-400 text-slate-400"
  }

  return (
    <div className="bg-white/5 border border-white/10 rounded-[20px] p-5 backdrop-blur-md shadow-2xl">
      <div className="text-[11px] font-black text-white/40 uppercase tracking-[0.1em] mb-4">
        ⏪ Event replay dashboard
      </div>
      
      {replay && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-2 mb-4">
          {[
            { label: "Total events", value: (replay.event_count??0).toLocaleString(), color: "text-sky-300" },
            { label: "Heal attempts", value: String(replay.healing_attempts), color: "text-pink-400" },
            { label: "Health score", value: replay.latest_health_score != null ? `${replay.latest_health_score}%` : "—", color: "text-green-300" },
            { label: "Last action", value: replay.last_action ?? "None", color: "text-amber-400" }
          ].map((s, i) => (
            <motion.div 
              initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.1 }}
              key={s.label} 
              className="bg-black/30 rounded-xl p-2.5"
            >
              <div className="text-[11px] text-white/40 mb-1">{s.label}</div>
              <div className={`text-base font-black truncate ${s.color}`}>{s.value}</div>
            </motion.div>
          ))}
        </div>
      )}

      <div className="text-[11px] font-semibold text-white/35 uppercase tracking-[0.08em] mb-2">
        Recent events
      </div>

      {!events.length ? (
        <div className="text-center py-5 text-white/30 text-[13px]">No events yet.</div>
      ) : (
        <div className="grid gap-1.5 max-h-[320px] overflow-y-auto pr-2 custom-scrollbar">
          {events.map((ev, i) => {
            const colorClass = ec(ev.event_type)
            return (
              <motion.div 
                initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.03 }}
                key={ev.event_id ?? i} 
                className="flex gap-2.5 items-start p-2 rounded-xl bg-black/20 hover:bg-black/40 transition-colors"
              >
                <span className={`w-2 h-2 rounded-full shrink-0 mt-1 shadow-[0_0_8px_currentColor] ${colorClass.split(' ')[0]}`} />
                <div className="flex-1 min-w-0">
                  <div className="flex justify-between gap-2">
                    <span className={`text-[12px] font-bold ${colorClass.split(' ')[1]}`}>{ev.event_type}</span>
                    <span className="text-[11px] text-white/30 shrink-0">{new Date(ev.occurred_at).toLocaleTimeString()}</span>
                  </div>
                  <div className="text-[11px] text-white/40 mt-0.5 truncate">
                    {ev.source} · {Object.entries(ev.payload??{}).slice(0,2).map(([k,v])=>`${k}: ${typeof v==="number"?Number(v).toFixed(2):v}`).join(" · ")}
                  </div>
                </div>
              </motion.div>
            )
          })}
        </div>
      )}
    </div>
  )
}
