import { useState } from "react"
interface HealCheck { ok: boolean }
interface HealResult { recovery_readiness?: number; active_anomalies?: number; last_heal_success?: boolean; checks?: Record<string,HealCheck>; error?: string }
interface Props { backendUrl: string }
export function HealingTrigger({ backendUrl }: Props) {
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<HealResult|null>(null)
  const [lastRun, setLastRun] = useState<string|null>(null)
  async function runHeal() {
    setRunning(true); setResult(null)
    try { const res = await fetch(`${backendUrl}/api/admin/healing/run`,{method:"POST"}); setResult(await res.json()); setLastRun(new Date().toLocaleTimeString()) }
    catch(e) { setResult({error:String(e)}) } finally { setRunning(false) }
  }
  async function checkStatus() {
    setRunning(true)
    try { const res = await fetch(`${backendUrl}/api/admin/healing/evaluate`); setResult(await res.json()); setLastRun(new Date().toLocaleTimeString()) }
    catch(e) { setResult({error:String(e)}) } finally { setRunning(false) }
  }
  const checks = result?.checks ?? {}
  const allOk = Object.values(checks).every(c=>c.ok)
  return (
    <div style={{ background:"rgba(255,255,255,0.03)", border:"1px solid rgba(255,255,255,0.08)", borderRadius:20, padding:"18px 20px" }}>
      <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:14 }}>
        <div>
          <div style={{ fontSize:11, fontWeight:700, color:"rgba(255,255,255,0.45)", textTransform:"uppercase", letterSpacing:"0.1em" }}>🔧 Self-healing control</div>
          {lastRun && <div style={{ fontSize:11, color:"rgba(255,255,255,0.3)", marginTop:2 }}>Last run: {lastRun}</div>}
        </div>
        <div style={{ display:"flex", gap:8 }}>
          <button onClick={checkStatus} disabled={running} style={{ padding:"7px 14px", borderRadius:10, border:"1px solid rgba(255,255,255,0.15)", background:"transparent", color:"rgba(255,255,255,0.7)", fontSize:12, cursor:running?"not-allowed":"pointer" }}>{running?"⏳":"🔍"} Check status</button>
          <button onClick={runHeal} disabled={running} style={{ padding:"7px 14px", borderRadius:10, border:"none", background:running?"rgba(255,255,255,0.1)":"linear-gradient(135deg,#639922,#86efac)", color:"white", fontWeight:700, fontSize:12, cursor:running?"not-allowed":"pointer" }}>{running?"⏳ Healing...":"⚡ Run heal cycle"}</button>
        </div>
      </div>
      {result && (
        <div style={{ display:"grid", gap:10 }}>
          {result.error ? <div style={{ padding:"10px 14px", borderRadius:12, background:"rgba(239,68,68,0.08)", border:"1px solid rgba(239,68,68,0.2)", fontSize:13, color:"#fca5a5" }}>❌ {result.error}</div> : (
            <>
              <div style={{ display:"grid", gridTemplateColumns:"repeat(3,1fr)", gap:8 }}>
                <div style={{ background:"rgba(0,0,0,0.3)", borderRadius:12, padding:"10px 14px" }}><div style={{ fontSize:11, color:"rgba(255,255,255,0.4)", marginBottom:4 }}>Readiness</div><div style={{ fontSize:20, fontWeight:700, color:(result.recovery_readiness??0)>=80?"#86efac":"#fbbf24" }}>{result.recovery_readiness??0}%</div></div>
                <div style={{ background:"rgba(0,0,0,0.3)", borderRadius:12, padding:"10px 14px" }}><div style={{ fontSize:11, color:"rgba(255,255,255,0.4)", marginBottom:4 }}>Anomalies</div><div style={{ fontSize:20, fontWeight:700, color:(result.active_anomalies??0)===0?"#86efac":"#f87171" }}>{result.active_anomalies??0}</div></div>
                <div style={{ background:"rgba(0,0,0,0.3)", borderRadius:12, padding:"10px 14px" }}><div style={{ fontSize:11, color:"rgba(255,255,255,0.4)", marginBottom:4 }}>Last heal</div><div style={{ fontSize:20, fontWeight:700, color:result.last_heal_success?"#86efac":"#f87171" }}>{result.last_heal_success?"✓ OK":"✗ Failed"}</div></div>
              </div>
              <div style={{ padding:"10px 14px", borderRadius:12, background:allOk?"rgba(34,197,94,0.06)":"rgba(239,68,68,0.06)", border:`1px solid ${allOk?"rgba(34,197,94,0.2)":"rgba(239,68,68,0.2)"}` }}>
                <div style={{ fontSize:11, fontWeight:700, color:allOk?"#86efac":"#fca5a5", textTransform:"uppercase", letterSpacing:"0.08em", marginBottom:8 }}>{allOk?"✅ All systems healthy":"⚠️ Issues detected"}</div>
                <div style={{ display:"flex", flexWrap:"wrap", gap:6 }}>
                  {Object.entries(checks).map(([name,check])=>(
                    <span key={name} style={{ fontSize:12, padding:"3px 10px", borderRadius:999, background:check.ok?"rgba(34,197,94,0.1)":"rgba(239,68,68,0.1)", color:check.ok?"#86efac":"#fca5a5", border:`1px solid ${check.ok?"rgba(34,197,94,0.2)":"rgba(239,68,68,0.2)"}` }}>{check.ok?"✓":"✗"} {name}</span>
                  ))}
                </div>
                <div style={{ fontSize:13, color:"rgba(200,240,200,0.7)", marginTop:8, lineHeight:1.6 }}>{allOk?"Your AI system is running cleanly. All services are responding normally.":"Some services need attention. Click Run heal cycle to attempt automatic recovery."}</div>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}
