import { useState } from "react"
import { useUiStore } from "../state/ui-store"
import { motion, AnimatePresence } from "framer-motion"
import { Button } from "@/components/ui/button"
import { Send, Activity, TerminalSquare } from "lucide-react"
import { cn } from "@/lib/utils"

export default function AgentPage() {
  const backendUrl = useUiStore((state) => state.backendUrl)

  const [messages, setMessages] = useState<Array<{ id: string; role: "user" | "assistant"; content: string }>>([])
  const [input, setInput] = useState("")
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const nextId = (role: "user" | "assistant") => `${role}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`

  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => setInput(e.target.value)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const prompt = input
    if (!prompt.trim()) return

    setMessages((prev) => [
      ...prev,
      { id: nextId("user"), role: "user", content: prompt },
    ])
    setInput("")
    setIsLoading(true)
    setError(null)

    try {
      const response = await fetch(`${backendUrl}/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt }),
      })

      if (!response.ok) {
        throw new Error(`Request failed: ${response.status}`)
      }

      const json = await response.json()
      // /generate returns {content, model} — content must be first in the chain,
      // otherwise every reply falls through to raw JSON.stringify.
      const text = json.content ?? json.response ?? json.answer ?? json.output ?? json.result ?? JSON.stringify(json, null, 2)

      setMessages((prev) => [
        ...prev,
        { id: nextId("assistant"), role: "assistant", content: String(text) },
      ])
    } catch (err) {
      setError(err instanceof Error ? err : new Error(String(err)))
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <motion.section 
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className="w-full p-6 md:p-10 glass-panel rounded-[24px] min-h-[calc(100vh-6rem)] my-4 mx-auto max-w-[1400px] flex flex-col overflow-hidden relative shadow-[0_0_50px_rgba(0,240,255,0.05)]"
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
          Send a live prompt to the backend agent and inspect the returned response in the Cyber Matrix.
        </p>
      </header>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8 w-full relative z-10 flex-1">
        
        {/* Prompt Input Area */}
        <motion.article 
          initial={{ opacity: 0, scale: 0.95, x: -20 }}
          animate={{ opacity: 1, scale: 1, x: 0 }}
          transition={{ delay: 0.1, type: "spring", bounce: 0.3 }}
          className="flex flex-col gap-4 p-6 glass-panel cyber-border rounded-2xl h-full"
        >
          <h2 className="text-[11px] text-cyan-400 uppercase tracking-[0.2em] font-black m-0 flex items-center gap-2">
            <TerminalSquare className="w-4 h-4" /> Operator Console
          </h2>
          <form onSubmit={handleSubmit} className="flex flex-col flex-1 gap-4">
            <textarea
              value={input}
              onChange={handleInputChange}
              className="w-full p-5 text-sm leading-relaxed text-white/90 bg-black/50 border border-cyan-500/20 rounded-xl min-h-[300px] flex-1 resize-none focus:outline-none focus:ring-2 focus:ring-cyan-400/50 focus:border-cyan-400 shadow-[inset_0_2px_20px_rgba(0,0,0,0.8)] backdrop-blur-xl transition-all font-mono"
              placeholder=">> INPUT_COMMAND..."
            />
            <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
              <Button
                type="submit"
                disabled={isLoading || !input.trim()}
                className="w-full h-14 bg-cyan-500 hover:bg-cyan-400 text-black font-black uppercase tracking-[0.2em] rounded-xl shadow-[0_0_20px_rgba(34,211,238,0.2)] transition-all disabled:opacity-50 disabled:shadow-none hover:shadow-[0_0_30px_rgba(34,211,238,0.4)] flex items-center justify-center gap-2"
              >
                {isLoading ? <Activity className="w-5 h-5 animate-spin-slow" /> : <Send className="w-5 h-5" />}
                {isLoading ? "Connecting to brain..." : "Execute Intent"}
              </Button>
            </motion.div>
          </form>
        </motion.article>

        {/* Live Interpretation Output */}
        <motion.article 
          initial={{ opacity: 0, scale: 0.95, x: 20 }}
          animate={{ opacity: 1, scale: 1, x: 0 }}
          transition={{ delay: 0.2, type: "spring", bounce: 0.3 }}
          className="flex flex-col gap-4 p-6 glass-panel cyber-border rounded-2xl h-full"
        >
          <h2 className="text-[11px] text-fuchsia-400 uppercase tracking-[0.2em] font-black m-0 flex items-center gap-2">
            <Activity className="w-4 h-4" /> Live Interpretation
          </h2>
          <div className="relative flex-1 w-full min-h-[300px] rounded-xl bg-black/60 border border-fuchsia-500/20 shadow-[inset_0_2px_20px_rgba(0,0,0,0.8)] overflow-hidden">
            <div className="absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-transparent via-fuchsia-500/50 to-transparent opacity-50" />
            
            <div className="absolute inset-0 overflow-y-auto m-0 whitespace-pre-wrap break-words p-6 text-cyan-50 font-mono text-[13px] scrollbar-thin scrollbar-thumb-fuchsia-500/20 scrollbar-track-transparent">
              <AnimatePresence mode="wait">
                {error && (
                  <motion.div
                    key="error"
                    initial={{ opacity: 0, x: -10 }}
                    animate={{ opacity: 1, x: 0 }}
                    className="text-red-400 p-4 bg-red-950/40 rounded-lg border border-red-500/30 backdrop-blur-sm mb-4"
                  >
                    [ERROR] {String(error.message)}
                  </motion.div>
                )}
                
                {messages.length === 0 && !isLoading && (
                  <motion.div key="empty" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
                    <span className="opacity-50 italic">Waiting for signal...</span>
                  </motion.div>
                )}

                {messages.map((m) => (
                  <motion.div
                    key={m.id}
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    className={`mb-6 ${m.role === 'user' ? 'text-cyan-400' : 'text-fuchsia-100'}`}
                  >
                    <div className="text-[10px] uppercase tracking-[0.2em] opacity-50 mb-1">
                      {m.role === 'user' ? 'Operator' : 'System'}
                    </div>
                    <div>{m.content}</div>
                  </motion.div>
                ))}

                {isLoading && (
                  <motion.div
                    key="loading"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    className="text-cyan-400/60 flex flex-col gap-2 mt-4"
                  >
                    <div className="flex items-center gap-2">
                      <span className="w-1.5 h-1.5 bg-cyan-400 rounded-full animate-bounce" />
                      <span className="w-1.5 h-1.5 bg-cyan-400 rounded-full animate-bounce" style={{animationDelay: '0.1s'}} />
                      <span className="w-1.5 h-1.5 bg-cyan-400 rounded-full animate-bounce" style={{animationDelay: '0.2s'}} />
                      <span className="uppercase tracking-widest ml-2">Receiving stream...</span>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </div>
        </motion.article>
      </div>
    </motion.section>
  )
}

