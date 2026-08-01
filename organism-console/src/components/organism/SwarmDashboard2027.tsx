import React, { useState, useEffect, useRef, useCallback } from 'react';
import { MemorySearchPanel } from '../MemorySearchPanel';

type AgentType = {
  id: string;
  role: string;
  description: string;
  model_role: string;
  model?: string;
  config: any;
};

const injectStyles = () => {
  if (typeof document === 'undefined') return;
  if (document.getElementById('zenith-2027-styles')) return;

  const style = document.createElement('style');
  style.id = 'zenith-2027-styles';
  style.innerHTML = `
    @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&display=swap');
    
    .glass-card-2027 {
      background: rgba(255, 255, 255, 0.04);
      backdrop-filter: blur(16px);
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-radius: 16px;
      padding: 24px;
      position: relative;
      overflow: hidden;
      transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
    }

    .glass-card-2027:hover {
      background: rgba(255, 255, 255, 0.06);
    }

    .scan-line-2027 {
      position: absolute;
      top: -100%;
      left: 0;
      width: 100%;
      height: 20%;
      background: linear-gradient(to bottom, transparent, rgba(255, 255, 255, 0.1), transparent);
      animation: scan2027 8s linear infinite;
      pointer-events: none;
    }

    @keyframes scan2027 {
      0% { top: -100%; }
      100% { top: 200%; }
    }

    .mono-text-2027 {
      font-family: 'JetBrains Mono', monospace;
    }

    .pulse-glow-2027 {
      animation: pulse2027 2.4s infinite cubic-bezier(0.4, 0, 0.6, 1);
    }

    @keyframes pulse2027 {
      0%, 100% { opacity: 1; transform: scale(1); }
      50% { opacity: 0.7; transform: scale(1.05); }
    }

    .particle-2027 {
      position: absolute;
      border-radius: 50%;
      background: white;
      opacity: 0.3;
      pointer-events: none;
    }

    @keyframes travel {
      0% { left: 0; opacity: 1; }
      100% { left: 100%; opacity: 0; }
    }
  `;
  document.head.appendChild(style);
};

const agentColors: Record<string, string> = {
  coordinator: '#7dd3fc',
  planner: '#a78bfa',
  executor: '#22c55e',
  coder: '#f472b6',
  'tool-runner': '#fb923c',
  debugger: '#ef4444',
  reviewer: '#fbbf24',
};

const agentOrder = ['coordinator', 'planner', 'researcher', 'executor', 'coder', 'tool-runner', 'debugger', 'reviewer'];

const RadarChart = ({ backendUrl }: { backendUrl: string }) => {
  const [scores, setScores] = useState<Record<string, number>>(
    agentOrder.reduce((acc, curr) => ({ ...acc, [curr]: 0 }), {})
  );

  useEffect(() => {
    const fetchScores = async () => {
      try {
        const [routerRes, agentsRes] = await Promise.all([
          fetch(`${backendUrl}/router`),
          fetch(`${backendUrl}/agents`),
        ]);
        if (!routerRes.ok || !agentsRes.ok) return;
        const [routerData, agentsData] = await Promise.all([routerRes.json(), agentsRes.json()]);

        const distribution: Record<string, number> = routerData.model_distribution || {};
        const agents: AgentType[] = Array.isArray(agentsData) ? agentsData : [];

        const agentModel: Record<string, string> = {};
        agents.forEach((a) => {
          const role = a.id || a.role || '';
          const model = (a.model_role || a.model || '').replace('openrouter/', '').split(':')[0];
          if (role && model) agentModel[role] = model;
        });

        const maxCount = Math.max(1, ...Object.values(distribution));
        setScores(prev => {
          const next = { ...prev };
          agentOrder.forEach((id) => {
            const model = agentModel[id];
            const count = (model && distribution[model]) || 0;
            next[id] = Math.round((count / maxCount) * 100);
          });
          return next;
        });
      } catch (e) { console.error("Error fetching radar scores:", e); }
    };

    fetchScores();
    const interval = setInterval(fetchScores, 10000);
    return () => clearInterval(interval);
  }, [backendUrl]);

  const size = 300;
  const center = size / 2;
  const radius = size * 0.4;
  const angleStep = (Math.PI * 2) / agentOrder.length;

  const points = agentOrder.map((agent, i) => {
    const value = scores[agent] / 100;
    const angle = i * angleStep - Math.PI / 2;
    const x = center + radius * value * Math.cos(angle);
    const y = center + radius * value * Math.sin(angle);
    return `${x},${y}`;
  });

  return (
    <div className="glass-card-2027" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
      <h3 style={{ margin: '0 0 16px', letterSpacing: '0.1em', textTransform: 'uppercase', fontSize: '14px', color: 'rgba(255,255,255,0.7)' }}>Model Performance Radar</h3>
      <div style={{ position: 'relative', width: size, height: size }}>
        {Array.from({ length: 15 }).map((_, i) => (
          <div key={i} className="particle-2027 pulse-glow-2027" style={{
            left: `${Math.random() * 100}%`,
            top: `${Math.random() * 100}%`,
            width: `${Math.random() * 3 + 1}px`,
            height: `${Math.random() * 3 + 1}px`,
            animationDelay: `${Math.random() * 2}s`
          }} />
        ))}
        <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
          {agentOrder.map((agent, i) => {
            const angle = i * angleStep - Math.PI / 2;
            const x = center + radius * Math.cos(angle);
            const y = center + radius * Math.sin(angle);
            return (
              <line key={agent} x1={center} y1={center} x2={x} y2={y} stroke="rgba(255,255,255,0.1)" strokeWidth="1" />
            );
          })}
          
          <polygon 
            points={points.join(' ')} 
            fill="rgba(255,255,255,0.1)" 
            stroke="rgba(255,255,255,0.5)" 
            strokeWidth="2"
            style={{ transition: 'all 1s ease-out' }}
          />

          {agentOrder.map((agent, i) => {
            const value = scores[agent] / 100;
            const angle = i * angleStep - Math.PI / 2;
            const x = center + radius * value * Math.cos(angle);
            const y = center + radius * value * Math.sin(angle);
            return (
              <circle key={`dot-${agent}`} cx={x} cy={y} r="4" fill={agentColors[agent]} style={{ transition: 'all 1s ease-out', boxShadow: `0 0 10px ${agentColors[agent]}` }} />
            );
          })}

          {agentOrder.map((agent, i) => {
             const angle = i * angleStep - Math.PI / 2;
             const x = center + (radius + 20) * Math.cos(angle);
             const y = center + (radius + 20) * Math.sin(angle);
             return (
               <text key={`label-${agent}`} x={x} y={y} fill={agentColors[agent]} fontSize="10" textAnchor="middle" dominantBaseline="middle" className="mono-text-2027">
                 {agent.substring(0, 4).toUpperCase()}
               </text>
             );
          })}
        </svg>
      </div>
    </div>
  );
};

const AgentPipeline = ({ backendUrl, latestHandoff }: { backendUrl: string, latestHandoff: any }) => {
  const [agents, setAgents] = useState<AgentType[]>([]);

  useEffect(() => {
    const fetchAgents = async () => {
      try {
        const res = await fetch(`${backendUrl}/agents`);
        if (!res.ok) throw new Error(`Backend responded with ${res.status}`);
        const data = await res.json();
        setAgents(data);
      } catch (e) { console.error("Error fetching agents:", e); }
    };
    fetchAgents();
    const int = setInterval(fetchAgents, 30000);
    return () => clearInterval(int);
  }, [backendUrl]);

  return (
    <div className="glass-card-2027" style={{ marginBottom: '24px' }}>
      <h3 style={{ margin: '0 0 24px', letterSpacing: '0.1em', fontSize: '14px', color: 'rgba(255,255,255,0.7)' }}>Live Swarm Pipeline</h3>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', position: 'relative', overflowX: 'auto', paddingBottom: '16px' }}>
        {agentOrder.map((agentId, index) => {
          const isActive = latestHandoff?.to === agentId || latestHandoff?.agent_id === agentId;
          const isDead = agents.length > 0 && !agents.find(a => a.id === agentId);
          const color = agentColors[agentId] || '#ffffff';
          const shadow = isActive ? `0 0 20px ${color}80` : 'none';

          return (
            <div key={agentId} style={{ display: 'flex', alignItems: 'center' }}>
              <div style={{
                display: 'flex', flexDirection: 'column', alignItems: 'center', 
                opacity: isDead ? 0.3 : 1, transition: 'all 0.3s', position: 'relative'
              }}>
                {isActive && latestHandoff?.task && (
                  <div style={{
                    position: 'absolute', top: '-40px', background: 'rgba(0,0,0,0.8)', border: `1px solid ${color}`, 
                    padding: '4px 8px', borderRadius: '4px', fontSize: '10px', whiteSpace: 'nowrap', zIndex: 10
                  }} className="mono-text-2027">
                    {latestHandoff.task.substring(0, 20)}...
                  </div>
                )}
                <div style={{
                  width: '40px', height: '40px', borderRadius: '50%', background: isActive ? color : 'rgba(255,255,255,0.1)',
                  border: `2px solid ${color}`, boxShadow: shadow, display: 'flex', alignItems: 'center', justifyContent: 'center'
                }}>
                  <span style={{ fontSize: '18px' }}>🤖</span>
                </div>
                <span className="mono-text-2027" style={{ marginTop: '8px', fontSize: '12px', color }}>
                  {agentId}
                </span>
              </div>
              {index < agentOrder.length - 1 && (
                <div style={{ width: '40px', height: '2px', background: 'rgba(255,255,255,0.2)', margin: '0 12px', position: 'relative', top: '-10px' }}>
                  {isActive && latestHandoff?.to && agentOrder.indexOf(latestHandoff.to) > index && (
                    <div style={{ position: 'absolute', width: '10px', height: '10px', background: color, borderRadius: '50%', top: '-4px', animation: 'travel 1s forwards' }} />
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};

const AnimatedNumber = ({ value }: { value: number }) => {
  const [display, setDisplay] = useState(value);
  
  useEffect(() => {
    let start = display;
    const end = value;
    if (start === end) return;
    
    const duration = 1000;
    const startTime = performance.now();
    let rafId = 0;
    
    const animate = (time: number) => {
      const progress = Math.min((time - startTime) / duration, 1);
      const ease = progress === 1 ? 1 : 1 - Math.pow(2, -10 * progress);
      setDisplay(Math.floor(start + (end - start) * ease));
      if (progress < 1) rafId = requestAnimationFrame(animate);
    };
    rafId = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(rafId);
  }, [value]);

  return <span>{display}</span>;
};

const MetricCard = ({ title, value, history, suffix = '' }: { title: string, value: number, history: number[], suffix?: string }) => {
  const max = Math.max(...history, 1);
  const min = Math.min(...history, 0);
  const range = max - min || 1;
  
  const points = history.map((val, i) => {
    const x = (i / (Math.max(history.length - 1, 1))) * 100;
    const y = 100 - ((val - min) / range) * 100;
    return `${x},${y}`;
  }).join(' ');

  return (
    <div className="glass-card-2027">
      <div className="scan-line-2027" />
      <h4 style={{ margin: '0 0 8px', fontSize: '12px', color: 'rgba(255,255,255,0.6)', textTransform: 'uppercase' }}>{title}</h4>
      <div style={{ fontSize: '32px', fontWeight: 'bold' }} className="mono-text-2027">
        <AnimatedNumber value={value} />{suffix}
      </div>
      <div style={{ marginTop: '16px', height: '30px', width: '100%' }}>
        <svg width="100%" height="100%" viewBox="0 0 100 100" preserveAspectRatio="none">
          <polyline points={points} fill="none" stroke="#7dd3fc" strokeWidth="3" vectorEffect="non-scaling-stroke" />
        </svg>
      </div>
    </div>
  );
};

const MetricsWall = ({ backendUrl }: { backendUrl: string }) => {
  const [metrics, setMetrics] = useState({
    swarmHealth: 100, activeModels: 0, eventsProcessed: 0, cacheHits: 0, successRate: 100, healingReadiness: 100
  });
  const [history, setHistory] = useState<Record<string, number[]>>({
    swarmHealth: [], activeModels: [], eventsProcessed: [], cacheHits: [], successRate: [], healingReadiness: []
  });

  const updateHistory = (key: string, val: number) => {
    setHistory(prev => {
      const arr = [...(prev[key] || []), val].slice(-20);
      return { ...prev, [key]: arr };
    });
  };

  useEffect(() => {
    const fetchStatus = async () => {
      try {
        const res = await fetch(`${backendUrl}/status`);
        if (!res.ok) throw new Error(`Backend responded with ${res.status}`);
        const data = await res.json();
        setMetrics(m => ({ ...m, activeModels: data.installed_model_count || 0, eventsProcessed: data.event_count || 0 }));
        updateHistory('activeModels', data.installed_model_count || 0);
        updateHistory('eventsProcessed', data.event_count || 0);
      } catch (e) { console.error("Error fetching status:", e); }
    };

    const fetchTools = async () => {
      try {
        const res = await fetch(`${backendUrl}/tools/cache`);
        if (!res.ok) throw new Error(`Backend responded with ${res.status}`);
        const data = await res.json();
        setMetrics(m => ({ ...m, cacheHits: data.cache_size || 0 }));
        updateHistory('cacheHits', data.cache_size || 0);
      } catch(e) { console.error("Error fetching tools:", e); }
    };

    const fetchTimeline = async () => {
      try {
        const res = await fetch(`${backendUrl}/timeline`);
        if (!res.ok) throw new Error(`Backend responded with ${res.status}`);
        const data = await res.json();
        const success = data.points?.[data.points.length-1]?.success_count || 0;
        setMetrics(m => ({ ...m, successRate: Math.min(100, success * 10) }));
        updateHistory('successRate', Math.min(100, success * 10));
      } catch(e) { console.error("Error fetching timeline:", e); }
    };

    const fetchHealing = async () => {
      try {
        const res = await fetch(`${backendUrl}/features/healing-readiness`);
        if (res.ok) {
          setMetrics(m => ({ ...m, healingReadiness: 100 }));
          updateHistory('healingReadiness', 100);
        }
      } catch(e) { console.error("Error fetching healing:", e); }
    };

    fetchStatus(); fetchTools(); fetchTimeline(); fetchHealing();
    
    const int5 = setInterval(fetchStatus, 30000);
    const int10 = setInterval(fetchTools, 30000);
    const int15 = setInterval(fetchTimeline, 30000);
    const int30 = setInterval(fetchHealing, 30000);

    return () => { clearInterval(int5); clearInterval(int10); clearInterval(int15); clearInterval(int30); };
  }, [backendUrl]);

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '16px', marginBottom: '24px' }}>
      <MetricCard title="Swarm Health Score" value={metrics.swarmHealth} history={history.swarmHealth} />
      <MetricCard title="Active Models" value={metrics.activeModels} history={history.activeModels} />
      <MetricCard title="Events Processed" value={metrics.eventsProcessed} history={history.eventsProcessed} />
      <MetricCard title="Tool Cache Hits" value={metrics.cacheHits} history={history.cacheHits} />
      <MetricCard title="Success Rate" value={metrics.successRate} history={history.successRate} suffix="%" />
      <MetricCard title="Healing Readiness" value={metrics.healingReadiness} history={history.healingReadiness} suffix="%" />
    </div>
  );
};

const AgentConsole = ({ backendUrl, latestHandoff }: { backendUrl: string, latestHandoff: any }) => {
  const [logs, setLogs] = useState<{ id: string, text: string, color: string }[]>([]);
  const [input, setInput] = useState('');
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!latestHandoff) return;
    const data = latestHandoff;
    let color = '#fff';
    if (data.type === 'agent_handoff') color = '#22d3ee';
    else if (data.type === 'tool_result') color = '#fef08a';
    else if (data.type === 'final') color = '#4ade80';
    else if (data.type === 'error') color = '#f87171';

    const timestamp = new Date().toISOString().substring(11, 19);
    const text = `[${timestamp}] [${data.agent_id || 'system'}] [${data.type}] ${data.content || data.task || ''}`;
    
    setLogs(prev => [...prev.slice(-99), { id: Math.random().toString(), text, color }]);
  }, [latestHandoff]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs]);

  const handleSend = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim()) return;
    const prompt = input;
    setInput('');
    
    const timestamp = new Date().toISOString().substring(11, 19);
    setLogs(prev => [...prev.slice(-99), { id: Math.random().toString(), text: `[${timestamp}] [user] > ${prompt}`, color: '#fff' }]);

    try {
      const res = await fetch(`${backendUrl}/agents/coordinator/step/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt, history: [] })
      });
      if (!res.body) return;
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed) continue;
          let chunk: any;
          try { chunk = JSON.parse(trimmed); }
          catch { continue; }
          const content = chunk.content ?? chunk.output ?? chunk.text;
          const type = chunk.type ?? 'agent';
          if (content) {
            const ts = new Date().toISOString().substring(11, 19);
            const agent = chunk.agent_id || chunk.agent || chunk.model || 'coordinator';
            const color = type === 'tool_result' ? '#fef08a' : type === 'error' ? '#f87171' : type === 'final' ? '#4ade80' : '#22d3ee';
            setLogs(prev => [...prev.slice(-99), { id: `${Date.now()}-${Math.random()}`, text: `[${ts}] [${agent}] [${type}] ${content}`, color }]);
          }
        }
      }
    } catch (err) { console.error("Error in agent console stream:", err); }
  };

  return (
    <div className="glass-card-2027" style={{ height: '300px', display: 'flex', flexDirection: 'column' }}>
      <h3 style={{ margin: '0 0 16px', letterSpacing: '0.1em', fontSize: '14px', color: 'rgba(255,255,255,0.7)' }}>Live Agent Console</h3>
      <div style={{ flex: 1, overflowY: 'auto', marginBottom: '16px', fontFamily: '"JetBrains Mono", monospace', fontSize: '12px' }}>
        {logs.map((log) => (
          <div key={log.id} style={{ color: log.color, marginBottom: '4px', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
            {log.text}
          </div>
        ))}
        <div ref={endRef} />
      </div>
      <form onSubmit={handleSend} style={{ display: 'flex' }}>
        <input 
          type="text" 
          value={input} 
          onChange={(e) => setInput(e.target.value)}
          placeholder="Send task to coordinator..."
          style={{ 
            flex: 1, padding: '12px', background: 'rgba(0,0,0,0.5)', border: '1px solid rgba(255,255,255,0.2)', 
            color: '#fff', borderRadius: '8px', outline: 'none', fontFamily: '"JetBrains Mono", monospace'
          }}
        />
        <button type="submit" style={{ 
          padding: '0 24px', marginLeft: '12px', background: '#7dd3fc', color: '#000', 
          border: 'none', borderRadius: '8px', fontWeight: 'bold', cursor: 'pointer'
        }}>
          SEND
        </button>
      </form>
    </div>
  );
};

export const SwarmDashboard2027 = ({ backendUrl }: { backendUrl: string }) => {
  const [latestHandoff, setLatestHandoff] = useState<any>(null);

  useEffect(() => {
    injectStyles();
    
    // Connect to SSE stream
    const source = new EventSource(`${backendUrl}/swarm/v10/stream`);
    source.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        // Stream emits {event, id, timestamp, payload}; the old handler looked for
        // data.type/data.agent_id which the v10 event bus never sends.
        const event = data.event ?? data.type;
        const payload = data.payload ?? {};
        const handoff = payload.agent_handoff
          ? { to: payload.agent_handoff.to, from: payload.agent_handoff.from, task: payload.agent_handoff.task }
          : { to: payload.to, from: payload.from, task: payload.task, agent_id: payload.agent_id };
        if (event === 'agent_handoff' || event === 'model_selected' || event === 'tool_result' || event === 'final' || (handoff && (handoff.to || handoff.agent_id))) {
          setLatestHandoff({ ...data, ...handoff });
        }
      } catch (err) { console.error("Error parsing SSE message:", err); }
    };
    
    return () => source.close();
  }, [backendUrl]);

  return (
    <div style={{ padding: '24px 0', borderBottom: '1px solid rgba(255,255,255,0.1)', marginBottom: '32px' }}>
      <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '32px' }}>
        <div>
          <h1 style={{ fontSize: '32px', fontWeight: 'bold', margin: '0 0 8px', letterSpacing: '0.05em' }}>ZENITH SWARM OS</h1>
          <div style={{ display: 'flex', alignItems: 'center', color: '#7dd3fc' }} className="mono-text-2027">
            <span className="pulse-glow-2027" style={{ width: '8px', height: '8px', borderRadius: '50%', background: '#7dd3fc', marginRight: '8px', display: 'inline-block' }} />
            SYSTEM ONLINE
          </div>
        </div>
      </header>

      <AgentPipeline backendUrl={backendUrl} latestHandoff={latestHandoff} />
      
      <MetricsWall backendUrl={backendUrl} />

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 350px', gap: '24px' }}>
        <AgentConsole backendUrl={backendUrl} latestHandoff={latestHandoff} />
        <RadarChart backendUrl={backendUrl} />
      </div>

      <div style={{ marginTop: '24px' }}>
        <MemorySearchPanel />
      </div>
    </div>
  );
};

