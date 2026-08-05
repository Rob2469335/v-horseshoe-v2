import { useChat } from '@ai-sdk/react'
import { DefaultChatTransport } from 'ai'
import { motion, AnimatePresence } from 'framer-motion'
import { Button } from '@/components/ui/button'
import { useMemo, Suspense, Component, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { z } from 'zod'
import { Send, Activity, TerminalSquare, AlertCircle, RefreshCcw } from 'lucide-react'
import { cn } from '@/lib/utils'

class ErrorBoundary extends Component<{children: ReactNode, fallback: ReactNode}, {hasError: boolean}> {
  constructor(props: any) { super(props); this.state = { hasError: false } }
  static getDerivedStateFromError(_error: any) { return { hasError: true } }
  render() {
    if (this.state.hasError) return this.props.fallback;
    return this.props.children;
  }
}

const SystemHealthSchema = z.object({
  module: z.string(),
  status: z.enum(['healthy', 'degraded', 'error']).optional(),
  latency: z.string().optional()
});

function SystemHealthUI({ toolInvocation }: { toolInvocation: any }) {
  const isComplete = toolInvocation.state === 'output-available' || toolInvocation.state === 'output-error' || toolInvocation.state === 'output-denied';
  const args = toolInvocation.input ?? {};
  const result = toolInvocation.output ?? {};

  if (isComplete) {
    try {
      SystemHealthSchema.parse({ module: args.module, status: result.status, latency: result.latency });
    } catch (e) {
      return (
        <motion.div initial={{opacity:0}} animate={{opacity:1}} className="text-red-400 text-xs mt-2 bg-red-900/30 p-3 rounded-lg border border-red-500/50 backdrop-blur-md">
          <AlertCircle className="w-4 h-4 inline mr-2" /> Zod Validation Failed: {String(e)}
        </motion.div>
      )
    }
  }

  return (
    <motion.div 
      layout
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      className="mt-4 p-5 glass-panel cyber-border rounded-2xl relative overflow-hidden"
    >
      <div className="absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-transparent via-cyan-400 to-transparent opacity-50" />
      
      <div className="text-[10px] font-black uppercase tracking-[0.2em] text-cyan-400 mb-4 flex justify-between items-center pb-3 border-b border-cyan-400/20">
        <span className="flex items-center gap-2">
          <span className="relative flex h-2 w-2">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-cyan-400 opacity-75"></span>
            <span className="relative inline-flex rounded-full h-2 w-2 bg-cyan-500"></span>
          </span>
          Live System HUD
        </span>
        {isComplete && (
          <motion.button 
            whileHover={{ scale: 1.05 }}
            whileTap={{ scale: 0.95 }}
            onClick={() => alert(`Sent reboot signal to ${args.module}`)}
            className="flex items-center gap-1.5 bg-cyan-500/10 hover:bg-cyan-500/20 text-cyan-300 border border-cyan-500/30 px-3 py-1.5 rounded-md transition-all shadow-[0_0_10px_rgba(0,240,255,0.1)] hover:shadow-[0_0_15px_rgba(0,240,255,0.3)]"
          >
            <RefreshCcw className="w-3 h-3" />
            REBOOT
          </motion.button>
        )}
      </div>
      
      {isComplete ? (
        <motion.div initial={{opacity:0, y:5}} animate={{opacity:1, y:0}} className="grid grid-cols-3 gap-6">
          <div className="flex flex-col gap-1">
            <span className="text-white/40 text-[9px] uppercase tracking-wider font-bold">Module</span>
            <span className="text-white font-mono text-sm">{args.module}</span>
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-white/40 text-[9px] uppercase tracking-wider font-bold">Status</span>
            <span className={cn(
              "font-bold text-sm uppercase tracking-wide",
              result.status === 'healthy' ? 'text-green-400 drop-shadow-[0_0_8px_rgba(74,222,128,0.5)]' : 'text-amber-400 drop-shadow-[0_0_8px_rgba(251,191,36,0.5)]'
            )}>
              {result.status}
            </span>
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-white/40 text-[9px] uppercase tracking-wider font-bold">Latency</span>
            <span className="text-fuchsia-400 font-mono text-sm drop-shadow-[0_0_8px_rgba(232,121,249,0.4)]">{result.latency}</span>
          </div>
        </motion.div>
      ) : (
        <div className="text-cyan-200/70 text-xs font-mono animate-pulse flex items-center gap-3">
          <Activity className="w-4 h-4 animate-spin-slow" />
          Analyzing telemetry for {args.module || 'system'}...
        </div>
      )}
    </motion.div>
  )
}

function ToolInvocationRenderer({ toolInvocation }: { toolInvocation: any }) {
  const toolName = toolInvocation.type === 'dynamic-tool'
    ? toolInvocation.toolName
    : typeof toolInvocation.type === 'string' && toolInvocation.type.startsWith('tool-')
      ? toolInvocation.type.slice('tool-'.length)
      : '';
  if (toolName === 'getSystemHealth') {
    return (
      <ErrorBoundary fallback={<div className="text-red-400 text-xs bg-red-900/30 p-2 rounded mt-2 border border-red-500/50"><AlertCircle className="inline w-3 h-3 mr-1"/> Error rendering HUD</div>}>
        <Suspense fallback={<div className="text-cyan-400/50 text-xs mt-2 animate-pulse font-mono">Initializing HUD layer...</div>}>
          <SystemHealthUI toolInvocation={toolInvocation} />
        </Suspense>
      </ErrorBoundary>
    )
  }
  return (
    <motion.div initial={{opacity:0, height:0}} animate={{opacity:1, height:'auto'}} className="mt-3 text-cyan-200/60 text-[11px] font-mono bg-cyan-950/30 border border-cyan-500/20 p-3 rounded-lg flex items-center gap-2">
      <TerminalSquare className="w-3 h-3" />
      &gt; EXECUTING_PROTOCOL: {toolName || 'unknown'}()
    </motion.div>
  )
}

export default function AgentPage() {
  const chatConfig = useMemo(() => ({ transport: new DefaultChatTransport({ api: '/api/chat' }) }), [])
  const { messages, sendMessage, status, error } = useChat(chatConfig)
  const [input, setInput] = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)
  const isLoading = status === 'submitted' || status === 'streaming'

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const text = input.trim()
    if (!text || isLoading) return
    setInput('')
    void sendMessage({ text })
  }

  return (
    <motion.section 
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className="w-full p-6 md:p-10 glass-panel rounded-[24px] h-[calc(100vh-2rem)] my-4 mx-auto max-w-[1400px] flex flex-col overflow-hidden relative shadow-[0_0_50px_rgba(0,240,255,0.05)]"
    >
      {/* Background Decorative Elements */}
      <div className="absolute top-[-20%] left-[-10%] w-[50%] h-[50%] bg-cyan-500/10 blur-[120px] rounded-full pointer-events-none" />
      <div className="absolute bottom-[-20%] right-[-10%] w-[50%] h-[50%] bg-fuchsia-500/10 blur-[120px] rounded-full pointer-events-none" />

      <header className="grid gap-2 mb-8 shrink-0 relative z-10">
        <div className="flex items-center gap-3 mb-1">
          <div className="w-2 h-8 bg-cyan-400 rounded-full shadow-[0_0_15px_rgba(34,211,238,0.6)]" />
          <h1 className="text-4xl font-black tracking-tight text-white m-0 drop-shadow-[0_2px_10px_rgba(255,255,255,0.2)]">Swarm UI <span className="text-cyan-400 font-light opacity-80">v2.0</span></h1>
        </div>
        <p className="text-cyan-100/60 max-w-[72ch] leading-relaxed text-sm font-medium tracking-wide">
          Generative UI Matrix. Awaiting commands.
        </p>
      </header>

      <div className="flex-1 overflow-y-auto mb-6 pr-4 space-y-6 scrollbar-thin scrollbar-thumb-cyan-500/20 scrollbar-track-transparent relative z-10">
        <AnimatePresence initial={false}>
          {messages.map((m) => (
            <motion.div 
              layout
              key={m.id}
              initial={{ opacity: 0, y: 20, scale: 0.95 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              transition={{ duration: 0.4, type: "spring", bounce: 0.3 }}
              className={cn(
                "p-5 rounded-2xl border backdrop-blur-md relative",
                m.role === 'user' 
                  ? "bg-cyan-500/10 border-cyan-400/30 text-white self-end ml-auto max-w-[85%] shadow-[0_4px_30px_rgba(0,240,255,0.05)]" 
                  : "bg-white/5 border-white/10 text-cyan-50 self-start mr-auto max-w-[85%] shadow-[0_4px_30px_rgba(255,255,255,0.02)]"
              )}
            >
              <div className={cn(
                "font-bold text-[10px] uppercase tracking-[0.2em] mb-3 flex items-center gap-2",
                m.role === 'user' ? "text-cyan-300" : "text-fuchsia-400"
              )}>
                {m.role === 'user' ? 'Operator' : 'Swarm Cortex'}
              </div>
              
              {m.parts.map((part, partIndex) => {
                if (part.type === 'text') {
                  return <p key={partIndex} className="whitespace-pre-wrap leading-relaxed m-0 text-sm">{part.text}</p>
                }
                if (part.type === 'dynamic-tool' || (typeof part.type === 'string' && part.type.startsWith('tool-'))) {
                  return <ToolInvocationRenderer key={(part as any).toolCallId ?? partIndex} toolInvocation={part} />
                }
                return null
              })}
            </motion.div>
          ))}
        </AnimatePresence>
        
        {isLoading && (
          <motion.div initial={{opacity:0}} animate={{opacity:1}} className="text-cyan-400/60 text-xs font-mono flex items-center gap-2 p-4">
            <span className="w-1.5 h-1.5 bg-cyan-400 rounded-full animate-bounce" />
            <span className="w-1.5 h-1.5 bg-cyan-400 rounded-full animate-bounce" style={{animationDelay: '0.1s'}} />
            <span className="w-1.5 h-1.5 bg-cyan-400 rounded-full animate-bounce" style={{animationDelay: '0.2s'}} />
            <span className="ml-2 uppercase tracking-widest">Processing</span>
          </motion.div>
        )}
        
        {error && (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="p-4 rounded-xl border border-red-500/50 bg-red-950/40 text-red-200 backdrop-blur-md flex items-center gap-3">
            <AlertCircle className="w-5 h-5 text-red-400" />
            <span className="font-mono text-sm">CRITICAL: {error.message}</span>
          </motion.div>
        )}
        <div ref={bottomRef} className="h-4" />
      </div>

      <form onSubmit={handleSubmit} className="shrink-0 flex gap-4 w-full relative z-10 group">
        <input
          aria-label="Agent Input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          className="w-full h-16 pl-6 pr-[140px] text-sm text-white bg-black/50 border border-cyan-500/20 rounded-[20px] focus:outline-none focus:ring-2 focus:ring-cyan-400/50 focus:border-cyan-400 shadow-[inset_0_2px_20px_rgba(0,0,0,0.8)] backdrop-blur-xl transition-all font-mono placeholder:text-cyan-100/20"
          placeholder=">> INPUT_COMMAND..."
        />
        <Button
          type="submit"
          disabled={isLoading || !input.trim()}
          className="absolute right-2 top-2 h-12 px-8 bg-cyan-500 hover:bg-cyan-400 text-black font-black uppercase tracking-[0.2em] rounded-2xl shadow-[0_0_20px_rgba(34,211,238,0.2)] transition-all disabled:opacity-50 disabled:shadow-none hover:shadow-[0_0_30px_rgba(34,211,238,0.4)] flex items-center gap-2 group-focus-within:border-white"
        >
          {isLoading ? <Activity className="w-4 h-4 animate-spin-slow" /> : <Send className="w-4 h-4" />}
          {isLoading ? "TX" : "SEND"}
        </Button>
      </form>
    </motion.section>
  )
}
