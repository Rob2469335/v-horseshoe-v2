import { useQuery } from "@tanstack/react-query"
import { useUiStore } from "../state/ui-store"
interface ReplayData { event_count:number; latest_health_score:number|null; healing_attempts:number; last_action:string|null; components:Record<string,unknown> }
interface EventsData { count:number; events:{event_id:string;event_type:string;occurred_at:string;source:string;payload:Record<string,unknown>}[] }
export function ReplayDashboard() {
  const backendUrl=useUiStore(s=>s.backendUrl)
  const rq=useQuery<ReplayData>({queryKey:["replay",backendUrl],queryFn:async()=>(await fetch(`${backendUrl}/admin/replay`)).json(),refetchInterval:30000})
  const eq=useQuery<EventsData>({queryKey:["events-recent",backendUrl],queryFn:async()=>(await fetch(`${backendUrl}/events?limit=20`)).json(),refetchInterval:15000})
  const replay=rq.data; const events=eq.data?.events??[]
  function ec(t:string){if(t.includes("evolve"))return"#a78bfa";if(t.includes("llm")||t.includes("generate"))return"#7dd3fc";if(t.includes("agent"))return"#86efac";if(t.includes("heal"))return"#f472b6";return"#94a3b8"}
  return (
    <div style={{ background:"rgba(255,255,255,0.03)", border:"1px solid rgba(255,255,255,0.08)", borderRadius:20, padding:"18px 20px" }}>
      <div style={{ fontSize:11, fontWeight:700, color:"rgba(255,255,255,0.45)", textTransform:"uppercase", letterSpacing:"0.1em", marginBottom:14 }}>⏪ Event replay dashboard</div>
      {replay&&<div style={{ display:"grid", gridTemplateColumns:"repeat(4,1fr)", gap:8, marginBottom:16 }}>
        {[{label:"Total events",value:(replay.event_count??0).toLocaleString(),color:"#7dd3fc"},{label:"Heal attempts",value:String(replay.healing_attempts),color:"#f472b6"},{label:"Health score",value:replay.latest_health_score!=null?`${replay.latest_health_score}%`:"—",color:"#86efac"},{label:"Last action",value:replay.last_action??"None",color:"#fbbf24"}].map(s=>(
          <div key={s.label} style={{ background:"rgba(0,0,0,0.3)", borderRadius:12, padding:"10px 12px" }}>
            <div style={{ fontSize:11, color:"rgba(255,255,255,0.4)", marginBottom:4 }}>{s.label}</div>
            <div style={{ fontSize:16, fontWeight:700, color:s.color, wordBreak:"break-word" }}>{s.value}</div>
          </div>
        ))}
      </div>}
      <div style={{ fontSize:11, fontWeight:600, color:"rgba(255,255,255,0.35)", textTransform:"uppercase", letterSpacing:"0.08em", marginBottom:8 }}>Recent events</div>
      {!events.length?<div style={{ textAlign:"center", padding:"20px 0", color:"rgba(255,255,255,0.3)", fontSize:13 }}>No events yet.</div>:(
        <div style={{ display:"grid", gap:6, maxHeight:320, overflowY:"auto" }}>
          {events.map((ev,i)=>(
            <div key={ev.event_id??i} style={{ display:"flex", gap:10, alignItems:"flex-start", padding:"8px 10px", borderRadius:10, background:"rgba(0,0,0,0.2)" }}>
              <span style={{ width:8, height:8, borderRadius:"50%", background:ec(ev.event_type), flexShrink:0, marginTop:4 }} />
              <div style={{ flex:1, minWidth:0 }}>
                <div style={{ display:"flex", justifyContent:"space-between", gap:8 }}>
                  <span style={{ fontSize:12, color:ec(ev.event_type), fontWeight:600 }}>{ev.event_type}</span>
                  <span style={{ fontSize:11, color:"rgba(255,255,255,0.3)", flexShrink:0 }}>{new Date(ev.occurred_at).toLocaleTimeString()}</span>
                </div>
                <div style={{ fontSize:11, color:"rgba(255,255,255,0.4)", marginTop:2 }}>{ev.source} · {Object.entries(ev.payload??{}).slice(0,2).map(([k,v])=>`${k}: ${typeof v==="number"?Number(v).toFixed(2):v}`).join(" · ")}</div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

