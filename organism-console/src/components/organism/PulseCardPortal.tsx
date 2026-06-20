import { useState } from "react"
export interface PulseCardData {
  key: string; label: string; value: string; sub: string
  accentColor: string; dotColor: string; accent: string; detail: string
  stats: { label: string; value: string }[]
  models?: string[]
  whatItMeans: string; rightNow: string; whatToDo: string
  actionClass: "action-good"|"action-warn"|"action-bad"
  actionText: string; askMore: string
}
const AC: Record<string,string> = { "action-good":"#639922","action-warn":"#BA7517","action-bad":"#A32D2D" }
export function PulseCardPortal({ cards, onAsk }: { cards: PulseCardData[]; onAsk?: (p:string)=>void }) {
  const [activeKey, setActiveKey] = useState<string|null>(null)
  return (
    <div style={{ display:"grid", gridTemplateColumns:"repeat(auto-fit,minmax(260px,1fr))", gap:12 }}>
      {cards.map(card => {
        const isOpen = activeKey === card.key
        return (
          <div key={card.key} onClick={() => setActiveKey(isOpen?null:card.key)}
            style={{ background:"rgba(255,255,255,0.04)", border:`1px solid ${isOpen?card.accentColor:"rgba(255,255,255,0.08)"}`, borderRadius:20, padding:"16px 18px", cursor:"pointer", transition:"border-color 0.2s", boxShadow:isOpen?`0 0 24px ${card.accentColor}33`:"none", position:"relative", overflow:"hidden" }}>
            <div style={{ position:"absolute", top:0, left:0, right:0, height:3, background:card.accentColor, borderRadius:"20px 20px 0 0" }} />
            <div style={{ display:"flex", justifyContent:"space-between", alignItems:"flex-start", marginTop:4 }}>
              <span style={{ fontSize:11, color:"rgba(255,255,255,0.55)", fontWeight:600, letterSpacing:"0.06em", textTransform:"uppercase" }}>
                <span style={{ display:"inline-block", width:8, height:8, borderRadius:"50%", background:card.dotColor, marginRight:6, verticalAlign:"middle" }} />{card.label}
              </span>
              <span style={{ fontSize:13, color:"rgba(255,255,255,0.4)", transform:isOpen?"rotate(180deg)":"none", transition:"transform 0.2s", display:"inline-block" }}>▾</span>
            </div>
            <div style={{ fontSize:24, fontWeight:600, color:"white", margin:"8px 0 4px" }}>{card.value}</div>
            <div style={{ fontSize:13, color:"rgba(255,255,255,0.55)" }}>{card.sub}</div>
            {isOpen && (
              <div onClick={e=>e.stopPropagation()} style={{ marginTop:16, paddingTop:16, borderTop:"1px solid rgba(255,255,255,0.08)" }}>
                <div style={{ display:"grid", gridTemplateColumns:"repeat(3,1fr)", gap:8, marginBottom:16 }}>
                  {card.stats.map(s => (
                    <div key={s.label} style={{ background:"rgba(0,0,0,0.3)", borderRadius:12, padding:"10px 12px" }}>
                      <div style={{ fontSize:11, color:"rgba(255,255,255,0.45)", marginBottom:4 }}>{s.label}</div>
                      <div style={{ fontSize:17, fontWeight:600, color:"white" }}>{s.value}</div>
                    </div>
                  ))}
                </div>
                <div style={{ marginBottom:12 }}>
                  <div style={{ fontSize:11, fontWeight:600, color:"rgba(255,255,255,0.4)", textTransform:"uppercase", letterSpacing:"0.08em", marginBottom:6 }}>What this means</div>
                  <div style={{ fontSize:13, color:"rgba(230,238,255,0.8)", lineHeight:1.7 }}>{card.whatItMeans}</div>
                </div>
                <div style={{ marginBottom:12 }}>
                  <div style={{ fontSize:11, fontWeight:600, color:"rgba(255,255,255,0.4)", textTransform:"uppercase", letterSpacing:"0.08em", marginBottom:6 }}>Right now</div>
                  <div style={{ fontSize:13, color:"rgba(230,238,255,0.8)", lineHeight:1.7 }}>{card.rightNow}</div>
                </div>
                {card.models && card.models.length > 0 && (
                  <div style={{ marginBottom:12 }}>
                    <div style={{ fontSize:11, fontWeight:600, color:"rgba(255,255,255,0.4)", textTransform:"uppercase", letterSpacing:"0.08em", marginBottom:6 }}>Models</div>
                    <div style={{ display:"flex", flexWrap:"wrap", gap:4 }}>
                      {card.models.slice(0,6).map(m => <span key={m} style={{ fontSize:11, padding:"3px 8px", borderRadius:999, background:"rgba(255,255,255,0.06)", border:"1px solid rgba(255,255,255,0.1)", color:"rgba(255,255,255,0.6)" }}>{m}</span>)}
                    </div>
                  </div>
                )}
                <div style={{ marginBottom:12 }}>
                  <div style={{ fontSize:11, fontWeight:600, color:"rgba(255,255,255,0.4)", textTransform:"uppercase", letterSpacing:"0.08em", marginBottom:6 }}>What should I do</div>
                  <div style={{ padding:"10px 14px", borderRadius:12, background:"rgba(0,0,0,0.3)", borderLeft:`3px solid ${AC[card.actionClass]}`, fontSize:13, color:"rgba(230,238,255,0.85)", lineHeight:1.6, marginBottom:8 }}>{card.actionText}</div>
                  <div style={{ fontSize:13, color:"rgba(230,238,255,0.7)", lineHeight:1.7 }}>{card.whatToDo}</div>
                </div>
                {onAsk && <button onClick={()=>onAsk(card.askMore)} style={{ display:"inline-flex", alignItems:"center", gap:6, fontSize:12, padding:"7px 14px", borderRadius:10, border:"1px solid rgba(255,255,255,0.15)", background:"rgba(255,255,255,0.05)", color:"rgba(255,255,255,0.7)", cursor:"pointer" }}>💬 Ask a follow-up →</button>}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
