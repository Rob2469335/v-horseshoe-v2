import { useEffect, useRef, useState, useCallback } from "react"

interface NodeData {
  id: string; label: string; sublabel: string
  x: number; y: number; radius: number
  color: string; glow: string
  health: number; activity: number; pulseRate: number
}
interface Pulse {
  id: string; fromId: string; toId: string
  progress: number; color: string; size: number; speed: number
}
interface LiveData {
  ollamaReachable: boolean; installedModels: number; eventCount: number
  traceCount: number; healingReady: number; successRate: number
  cacheSize: number; visionAvailable: boolean
  routerStatus?: string; routerRouted?: number; routerModel?: string
  criticAcceptRate?: number; criticStatus?: string
}
interface Props { backendUrl: string; liveData?: LiveData }

const EDGES: [string,string][] = [
  ["router","ollama"],["router","critic"],["critic","memory"],
  ["memory","qdrant"],["healer","router"],["healer","ollama"],
  ["healer","qdrant"],["router","memory"],
]

function buildNodes(live: LiveData): NodeData[] {
  const W=900,H=520
  // When there's no data yet, default critic to online/healthy rather than dead-zero
  const criticHealth = live.criticAcceptRate !== undefined ? live.criticAcceptRate : (live.successRate > 0 ? live.successRate : (live.traceCount === 0 ? 100 : live.successRate))
  const criticActivity = live.criticStatus === "active" ? Math.max(20, criticHealth) : 20
  const routerHealth = live.routerStatus === "active" ? 100 : (live.ollamaReachable ? 100 : 40)
  const routerActivity = live.routerStatus === "active" ? 90 : 30
  
  return [
    { id:"router", label:"Router", sublabel:live.routerStatus === "active" && typeof live.routerRouted === "number" ? `${live.routerRouted} routed` : "model selector", x:W*0.50, y:H*0.38, radius:38, color:"#7dd3fc", glow:"rgba(125,211,252,0.5)", health:routerHealth, activity:routerActivity, pulseRate:1.8 },
    { id:"ollama", label:"Ollama", sublabel:`${live.installedModels} models`, x:W*0.78, y:H*0.22, radius:44, color:"#22c55e", glow:"rgba(34,197,94,0.5)", health:live.ollamaReachable?100:0, activity:live.ollamaReachable?85:0, pulseRate:2.2 },
    { id:"critic", label:"Critic", sublabel:`${criticHealth}% accept`, x:W*0.78, y:H*0.62, radius:34, color:"#f472b6", glow:"rgba(244,114,182,0.5)", health:criticHealth, activity:criticActivity, pulseRate:1.4 },
    { id:"memory", label:"Memory", sublabel:`${live.eventCount.toLocaleString()} events`, x:W*0.50, y:H*0.74, radius:36, color:"#a78bfa", glow:"rgba(167,139,250,0.5)", health:live.eventCount>0?100:50, activity:live.eventCount>100?80:40, pulseRate:1.0 },
    { id:"qdrant", label:"Qdrant", sublabel:`${live.cacheSize} cached`, x:W*0.22, y:H*0.62, radius:32, color:"#fb923c", glow:"rgba(251,146,60,0.5)", health:100, activity:live.cacheSize>0?70:30, pulseRate:0.8 },
    { id:"healer", label:"Healer", sublabel:`${live.healingReady}% ready`, x:W*0.22, y:H*0.22, radius:30, color:"#34d399", glow:"rgba(52,211,153,0.5)", health:live.healingReady, activity:live.healingReady>80?60:20, pulseRate:0.6 },
  ]
}


let _pc=0
function makePulse(fromId:string,toId:string,color:string):Pulse {
  return { id:`p${_pc++}`, fromId, toId, progress:0, color, size:4+Math.random()*4, speed:0.008+Math.random()*0.006 }
}

export function LivingNervousSystem({ backendUrl, liveData }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const animRef = useRef<number>(0)
  const timeRef = useRef(0)
  const pulsesRef = useRef<Pulse[]>([])
  const spawnRef = useRef(0)
  const [selectedNode, setSelectedNode] = useState<NodeData|null>(null)
  const [nodeDetail, setNodeDetail] = useState<string|null>(null)
  const [loading, setLoading] = useState(false)

  const live: LiveData = liveData ?? { ollamaReachable:false, installedModels:0, eventCount:0, traceCount:0, healingReady:100, successRate:0, cacheSize:0, visionAvailable:false }
  const nodes = buildNodes(live)
  const getNode = useCallback((id:string)=>nodes.find(n=>n.id===id),[nodes])

  async function handleNodeClick(node: NodeData) {
    setSelectedNode(node); setLoading(true); setNodeDetail(null)
    const prompts: Record<string,string> = {
      ollama: `You are the Ollama inference engine. Report your status in 3 bullet points. Models: ${live.installedModels}. Reachable: ${live.ollamaReachable}. Be concise.`,
      router: `You are the model router. Explain what you do in 2 sentences. You have routed ${live.traceCount} traces with ${live.successRate}% success.`,
      critic: `You are the critic evaluator. Current acceptance rate: ${live.successRate}%. Give a 2 sentence quality assessment.`,
      memory: `You are the memory subsystem. ${live.eventCount} events stored, ${live.cacheSize} cached. Explain in 2 sentences what this memory does.`,
      qdrant: `You are the Qdrant vector database. ${live.cacheSize} vectors cached. Explain semantic search in 2 sentences.`,
      healer: `You are the self-healing subsystem. Recovery readiness: ${live.healingReady}%. Report what you monitor and whether intervention is needed.`,
    }
    try {
      const res = await fetch(`${backendUrl}/generate`, { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({ prompt: prompts[node.id] ?? `Report status of ${node.label}.` }) })
      const data = await res.json()
      setNodeDetail(data.content ?? "No response")
    } catch(e) { setNodeDetail(`Could not reach ${node.label}: ${e}`) }
    finally { setLoading(false) }
  }

  useEffect(()=>{
    const canvas = canvasRef.current; if(!canvas) return
    const ctx = canvas.getContext("2d"); if(!ctx) return
    const W=canvas.width, H=canvas.height

    function drawEdge(from:NodeData,to:NodeData) {
      ctx!.beginPath(); ctx!.moveTo(from.x,from.y); ctx!.lineTo(to.x,to.y)
      ctx!.strokeStyle="rgba(255,255,255,0.06)"; ctx!.lineWidth=1; ctx!.stroke()
    }

    function drawNode(node:NodeData,t:number) {
      const breathe=1+Math.sin(t*node.pulseRate)*0.06*(node.activity/100)
      const r=node.radius*breathe
      const ha=0.3+(node.health/100)*0.7
      const grad=ctx!.createRadialGradient(node.x,node.y,r*0.4,node.x,node.y,r*2.2)
      grad.addColorStop(0,node.glow.replace("0.5)",`${ha*0.5})`))
      grad.addColorStop(1,"transparent")
      ctx!.beginPath(); ctx!.arc(node.x,node.y,r*2.2,0,Math.PI*2); ctx!.fillStyle=grad; ctx!.fill()
      ctx!.beginPath(); ctx!.arc(node.x,node.y,r,0,Math.PI*2); ctx!.fillStyle="rgba(10,16,32,0.85)"; ctx!.fill()
      ctx!.strokeStyle=node.color; ctx!.lineWidth=2; ctx!.globalAlpha=ha; ctx!.stroke(); ctx!.globalAlpha=1
      ctx!.beginPath(); ctx!.arc(node.x,node.y,r+4,-Math.PI/2,-Math.PI/2+(Math.PI*2*node.health/100))
      ctx!.strokeStyle=node.color; ctx!.lineWidth=3; ctx!.globalAlpha=0.5; ctx!.stroke(); ctx!.globalAlpha=1
      ctx!.fillStyle="white"; ctx!.font=`700 ${Math.round(r*0.38)}px system-ui`; ctx!.textAlign="center"; ctx!.textBaseline="middle"
      ctx!.fillText(node.label,node.x,node.y-4)
      ctx!.fillStyle=node.color; ctx!.font=`500 ${Math.round(r*0.28)}px system-ui`
      ctx!.fillText(node.sublabel,node.x,node.y+r*0.4)
      const np=Math.floor(node.activity/20)
      for(let i=0;i<np;i++){
        const angle=t*(0.4+i*0.15)+(i*Math.PI*2/np)
        const pr=r+10+i*3
        ctx!.beginPath(); ctx!.arc(node.x+Math.cos(angle)*pr,node.y+Math.sin(angle)*pr,2,0,Math.PI*2)
        ctx!.fillStyle=node.color; ctx!.globalAlpha=0.6; ctx!.fill(); ctx!.globalAlpha=1
      }
    }

    function drawPulse(pulse:Pulse) {
      const from=getNode(pulse.fromId),to=getNode(pulse.toId); if(!from||!to) return
      const x=from.x+(to.x-from.x)*pulse.progress, y=from.y+(to.y-from.y)*pulse.progress
      const g=ctx!.createRadialGradient(x,y,0,x,y,pulse.size*3)
      g.addColorStop(0,pulse.color); g.addColorStop(1,"transparent")
      ctx!.beginPath(); ctx!.arc(x,y,pulse.size*3,0,Math.PI*2); ctx!.fillStyle=g; ctx!.fill()
      ctx!.beginPath(); ctx!.arc(x,y,pulse.size,0,Math.PI*2); ctx!.fillStyle=pulse.color; ctx!.fill()
    }

    function frame(ts:number) {
      const dt=Math.min((ts-timeRef.current)/1000,0.05); timeRef.current=ts; const t=ts/1000
      ctx!.clearRect(0,0,W,H)
      ctx!.strokeStyle="rgba(255,255,255,0.02)"; ctx!.lineWidth=1
      for(let x=0;x<W;x+=60){ctx!.beginPath();ctx!.moveTo(x,0);ctx!.lineTo(x,H);ctx!.stroke()}
      for(let y=0;y<H;y+=60){ctx!.beginPath();ctx!.moveTo(0,y);ctx!.lineTo(W,y);ctx!.stroke()}
      EDGES.forEach(([a,b])=>{ const na=getNode(a),nb=getNode(b); if(na&&nb) drawEdge(na,nb) })
      pulsesRef.current=pulsesRef.current.filter(p=>p.progress<1)
      pulsesRef.current.forEach(p=>{ p.progress+=p.speed; drawPulse(p) })
      spawnRef.current+=dt
      const rate=live.ollamaReachable?0.8:2.5
      if(spawnRef.current>rate&&pulsesRef.current.length<20) {
        spawnRef.current=0
        const edge=EDGES[Math.floor(Math.random()*EDGES.length)]
        const fn=getNode(edge[0]); pulsesRef.current.push(makePulse(edge[0],edge[1],fn?.color??"#7dd3fc"))
        if(Math.random()>0.5) pulsesRef.current.push(makePulse(edge[1],edge[0],getNode(edge[1])?.color??"#a78bfa"))
      }
      nodes.forEach(n=>drawNode(n,t))
      animRef.current=requestAnimationFrame(frame)
    }

    animRef.current=requestAnimationFrame(frame)
    return()=>cancelAnimationFrame(animRef.current)
  },[live.ollamaReachable,live.traceCount,live.successRate,live.eventCount,live.cacheSize,live.healingReady,live.installedModels])

  function handleCanvasClick(e:React.MouseEvent<HTMLCanvasElement>) {
    const rect=canvasRef.current!.getBoundingClientRect()
    const mx=(e.clientX-rect.left)*(900/rect.width), my=(e.clientY-rect.top)*(520/rect.height)
    for(const node of nodes) {
      const dx=mx-node.x,dy=my-node.y
      if(Math.sqrt(dx*dx+dy*dy)<node.radius+16){ handleNodeClick(node); return }
    }
    setSelectedNode(null); setNodeDetail(null)
  }

  return (
    <div style={{position:"relative"}}>
      <div style={{fontSize:11,fontWeight:700,color:"rgba(255,255,255,0.35)",textTransform:"uppercase",letterSpacing:"0.12em",marginBottom:10}}>
        🧠 Living nervous system — click any node to wake it
      </div>
      <div style={{position:"relative",borderRadius:24,overflow:"hidden",border:"1px solid rgba(255,255,255,0.06)",background:"rgba(3,7,18,0.9)"}}>
        <canvas ref={canvasRef} width={900} height={520} onClick={handleCanvasClick} style={{width:"100%",height:"auto",display:"block",cursor:"crosshair"}} />
        {selectedNode && (
          <div style={{position:"absolute",top:12,right:12,width:280,background:"rgba(6,12,28,0.96)",border:`1px solid ${selectedNode.color}55`,borderRadius:18,padding:16,boxShadow:`0 0 40px ${selectedNode.glow}`}}>
            <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:10}}>
              <div>
                <div style={{fontSize:11,color:selectedNode.color,fontWeight:800,textTransform:"uppercase",letterSpacing:"0.1em"}}>{selectedNode.id}</div>
                <div style={{fontSize:18,color:"white",fontWeight:800}}>{selectedNode.label}</div>
              </div>
              <button onClick={()=>{setSelectedNode(null);setNodeDetail(null)}} style={{background:"none",border:"none",color:"rgba(255,255,255,0.4)",cursor:"pointer",fontSize:18}}>×</button>
            </div>
            <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:6,marginBottom:12}}>
              <div style={{background:"rgba(0,0,0,0.4)",borderRadius:10,padding:"8px 10px"}}>
                <div style={{fontSize:10,color:"rgba(255,255,255,0.4)",marginBottom:2}}>Health</div>
                <div style={{fontSize:18,fontWeight:800,color:selectedNode.health>70?"#22c55e":selectedNode.health>40?"#fbbf24":"#f87171"}}>{selectedNode.health}%</div>
              </div>
              <div style={{background:"rgba(0,0,0,0.4)",borderRadius:10,padding:"8px 10px"}}>
                <div style={{fontSize:10,color:"rgba(255,255,255,0.4)",marginBottom:2}}>Activity</div>
                <div style={{fontSize:18,fontWeight:800,color:selectedNode.color}}>{selectedNode.activity}%</div>
              </div>
            </div>
            <div style={{fontSize:12,color:"rgba(200,215,255,0.7)",lineHeight:1.7,marginBottom:10}}>
              {loading ? <span style={{color:selectedNode.color}}>⏳ Asking {selectedNode.label}...</span>
                : nodeDetail ? nodeDetail
                : <span style={{color:"rgba(255,255,255,0.4)"}}>Waking up {selectedNode.label}...</span>}
            </div>
            <div style={{height:4,borderRadius:999,background:"rgba(255,255,255,0.08)",overflow:"hidden"}}>
              <div style={{height:"100%",width:`${selectedNode.health}%`,background:selectedNode.color,borderRadius:999,transition:"width 0.5s"}} />
            </div>
          </div>
        )}
      </div>
      <div style={{display:"flex",flexWrap:"wrap",gap:8,marginTop:10}}>
        {nodes.map(n=>(
          <div key={n.id} onClick={()=>handleNodeClick(n)} style={{display:"flex",alignItems:"center",gap:6,padding:"5px 12px",borderRadius:999,background:"rgba(255,255,255,0.04)",border:`1px solid ${n.color}33`,cursor:"pointer",fontSize:12}}>
            <span style={{width:8,height:8,borderRadius:"50%",background:n.color,boxShadow:`0 0 8px ${n.color}`}} />
            <span style={{color:"rgba(255,255,255,0.7)"}}>{n.label}</span>
            <span style={{color:n.color,fontWeight:700}}>{n.health}%</span>
          </div>
        ))}
      </div>
    </div>
  )
}
