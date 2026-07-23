import { useState, useRef, useEffect } from "react"
import { useMutation } from "@tanstack/react-query"
import { api } from "../lib/api"
import type { ChatResponse } from "../lib/types"
import { useUiStore } from "../state/ui-store"
import { motion, AnimatePresence } from "framer-motion"
import { Button } from "@/components/ui/button"
import { Send, Activity, TerminalSquare } from "lucide-react"
import { cn } from "@/lib/utils"

function getDisplayText(data: ChatResponse | undefined) {
  if (!data) return ""
  return String(
    data.response ??
      data.answer ??
      data.output ??
      data.result ??
      JSON.stringify(data, null, 2),
  )
}

export default function AgentPage() {
  const backendUrl = useUiStore((state) => state.backendUrl)
  const [message, setMessage] = useState("")
  const [lastResponse, setLastResponse] = useState<ChatResponse | undefined>(undefined)

  const chatMutation = useMutation<ChatResponse, Error, string>({
    mutationFn: (nextMessage: string) => api.sendChat(backendUrl, nextMessage),
    onSuccess: (data: ChatResponse) => setLastResponse(data),
  })

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
          <textarea
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            className="w-full p-5 text-sm leading-relaxed text-white/90 bg-black/50 border border-cyan-500/20 rounded-xl min-h-[300px] flex-1 resize-none focus:outline-none focus:ring-2 focus:ring-cyan-400/50 focus:border-cyan-400 shadow-[inset_0_2px_20px_rgba(0,0,0,0.8)] backdrop-blur-xl transition-all font-mono"
            placeholder=">> INPUT_COMMAND..."
          />
          <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
            <Button
              onClick={() => chatMutation.mutate(message)}
              disabled={chatMutation.isPending || !message.trim()}
              className="w-full h-14 bg-cyan-500 hover:bg-cyan-400 text-black font-black uppercase tracking-[0.2em] rounded-xl shadow-[0_0_20px_rgba(34,211,238,0.2)] transition-all disabled:opacity-50 disabled:shadow-none hover:shadow-[0_0_30px_rgba(34,211,238,0.4)] flex items-center justify-center gap-2"
            >
              {chatMutation.isPending ? <Activity className="w-5 h-5 animate-spin-slow" /> : <Send className="w-5 h-5" />}
              {chatMutation.isPending ? "Connecting to brain..." : "Execute Intent"}
            </Button>
          </motion.div>
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
            
            <pre className="absolute inset-0 overflow-y-auto m-0 whitespace-pre-wrap break-words p-6 text-cyan-50 font-mono text-[13px] scrollbar-thin scrollbar-thumb-fuchsia-500/20 scrollbar-track-transparent">
              <AnimatePresence mode="wait">
                {chatMutation.isPending ? (
                  <motion.div
                    key="loading"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    className="text-cyan-400/60 flex flex-col gap-2"
                  >
                    <div className="flex items-center gap-2">
                      <span className="w-1.5 h-1.5 bg-cyan-400 rounded-full animate-bounce" />
                      <span className="w-1.5 h-1.5 bg-cyan-400 rounded-full animate-bounce" style={{animationDelay: '0.1s'}} />
                      <span className="w-1.5 h-1.5 bg-cyan-400 rounded-full animate-bounce" style={{animationDelay: '0.2s'}} />
                      <span className="uppercase tracking-widest ml-2">Receiving stream...</span>
                    </div>
                  </motion.div>
                ) : chatMutation.isError ? (
                  <motion.div
                    key="error"
                    initial={{ opacity: 0, x: -10 }}
                    animate={{ opacity: 1, x: 0 }}
                    className="text-red-400 p-4 bg-red-950/40 rounded-lg border border-red-500/30 backdrop-blur-sm"
                  >
                    [ERROR] {String(chatMutation.error.message)}
                  </motion.div>
                ) : (
                  <motion.div
                    key="result"
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                  >
                    {getDisplayText(lastResponse) || <span className="opacity-50 italic">Waiting for signal...</span>}
                  </motion.div>
                )}
              </AnimatePresence>
            </pre>
          </div>
        </motion.article>
      </div>
    </motion.section>
  )
}
