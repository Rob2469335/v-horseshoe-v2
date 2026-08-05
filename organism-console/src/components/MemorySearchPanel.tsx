import { useState } from "react"
import { useUiStore } from "../state/ui-store"
import { motion, AnimatePresence } from "framer-motion"

interface MemoryResult { id:string; score:number; text:string; source:string; sender?:string; timestamp:string }

export function MemorySearchPanel() {
  const backendUrl = useUiStore(s=>s.backendUrl)
  const [query, setQuery] = useState("")
  const [results, setResults] = useState<MemoryResult[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string|null>(null)
  const [searched, setSearched] = useState(false)

  async function search() {
    if(!query.trim()) return
    setLoading(true)
    setError(null)
    try { 
      const res = await fetch(`${backendUrl}/memory/search?q=${encodeURIComponent(query)}&limit=8`)
      const data = await res.json()
      if(data.error) setError(data.error)
      const items = (data.results ?? []).map((r: MemoryResult) => ({
        ...r,
        // BUG FIX: backend sends `sender`, UI reads `source`
        source: r.source ?? r.sender ?? "system"
      }))
      setResults(items)
      setSearched(true) 
    }
    catch(e){
      setError(String(e))
      setResults([])
    } finally {
      setLoading(false)
    }
  }

  function sc(s:number){
    return s >= 0.8 ? "text-green-300" : s >= 0.6 ? "text-amber-400" : "text-red-400"
  }

  return (
    <div className="bg-white/5 border border-white/10 rounded-[20px] p-5 backdrop-blur-md shadow-2xl">
      <div className="text-[11px] font-black text-white/40 uppercase tracking-[0.1em] mb-3">
        🧬 Semantic memory search
      </div>
      <div className="text-[13px] text-blue-100/60 mb-4 leading-relaxed">
        Search your AI long-term memory stored in Qdrant.
      </div>
      
      <div className="flex gap-2 mb-4">
        <input 
          value={query} 
          onChange={e=>setQuery(e.target.value)} 
          onKeyDown={e=>e.key==="Enter"&&search()} 
          placeholder="e.g. fitness optimization, coding task..." 
          className="flex-1 px-3.5 py-2.5 rounded-xl border border-white/10 bg-black/30 text-white text-[13px] focus:outline-none focus:ring-2 focus:ring-sky-400 focus:border-transparent transition-all placeholder:text-white/20"
        />
        <motion.button 
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
          onClick={search} 
          disabled={loading||!query.trim()} 
          className="px-4 py-2.5 rounded-xl border-none bg-gradient-to-br from-sky-500 to-sky-300 text-black font-black text-[13px] cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed shadow-[0_0_15px_rgba(56,189,248,0.2)]"
        >
          {loading ? "⏳" : "🔍"} Search
        </motion.button>
      </div>

      <AnimatePresence>
        {error && (
          <motion.div 
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="px-3.5 py-2.5 rounded-xl bg-red-500/10 border border-red-500/20 text-[13px] text-red-400 mb-3 overflow-hidden"
          >
            ⚠️ {error}
          </motion.div>
        )}
      </AnimatePresence>

      {searched && !results.length && !error && (
        <motion.div 
          initial={{ opacity: 0 }} animate={{ opacity: 1 }}
          className="text-center py-6 text-white/30 text-[14px]"
        >
          No memories found. Run more agent tasks to build memory.
        </motion.div>
      )}

      {results.length > 0 && (
        <div className="grid gap-2">
          {results.map((r, i) => (
            <motion.div 
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: i * 0.05 }}
              key={r.id ?? i} 
              className="bg-black/25 border border-white/5 rounded-[14px] p-3 backdrop-blur-sm hover:bg-black/40 transition-colors"
            >
              <div className="flex justify-between mb-1.5">
                <span className="text-[11px] text-white/40 uppercase font-bold tracking-wider">{r.source}</span>
                <span className={`text-[12px] font-black ${sc(r.score)}`}>
                  Match: {Math.round(r.score*100)}%
                </span>
              </div>
              <div className="text-[13px] text-blue-50/80 leading-relaxed">
                {r.text || "(no text)"}
              </div>
              {r.timestamp && (
                <div className="text-[11px] text-white/20 mt-1.5">
                  {r.timestamp}
                </div>
              )}
            </motion.div>
          ))}
        </div>
      )}
    </div>
  )
}
