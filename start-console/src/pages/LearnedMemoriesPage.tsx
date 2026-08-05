import { useQuery } from "@tanstack/react-query"
import { api } from "../lib/api"
import { useUiStore } from "../state/ui-store"

type MemoriesResponse = {
  status: string
  data: Record<string, any[]>
}

export default function LearnedMemoriesPage() {
  const backendUrl = useUiStore((state) => state.backendUrl)

  const { data, isLoading, error } = useQuery({
    queryKey: ["memories", backendUrl],
    queryFn: () => api.getMemories<MemoriesResponse>(backendUrl),
    retry: 1,
    refetchInterval: 30000
  })

  if (isLoading) {
    return (
      <div className="flex-1 overflow-auto p-6 bg-[#0a0f18] text-white flex items-center justify-center">
        <div className="animate-pulse text-sky-400 font-bold uppercase tracking-widest">Loading Memories...</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex-1 overflow-auto p-6 bg-[#0a0f18] text-white">
        <div className="p-4 rounded-xl bg-red-500/10 border border-red-500/20 text-red-400">
          Error loading memories. Check backend connection.
        </div>
      </div>
    )
  }

  const memoryData = data?.data || {}
  const collections = Object.keys(memoryData).filter(k => memoryData[k].length > 0)

  return (
    <div className="flex-1 overflow-auto p-6 lg:p-8 bg-gradient-to-br from-[#0a0f18] to-[#0f172a] text-white">
      <div className="max-w-7xl mx-auto space-y-8">
        
        <header className="mb-10">
          <h1 className="text-3xl font-black text-transparent bg-clip-text bg-gradient-to-r from-sky-400 to-indigo-400 mb-2">
            Learned Memories
          </h1>
          <p className="text-white/60">
            Insights, self-reflections, and distilled system rules persisted across sessions.
          </p>
        </header>

        {collections.length === 0 ? (
          <div className="p-12 text-center border border-white/5 bg-white/5 rounded-2xl">
            <h3 className="text-xl font-bold text-white/40 mb-2">No Memories Found</h3>
            <p className="text-white/30">Your Swarm hasn't persisted any insights yet.</p>
          </div>
        ) : (
          collections.map((collectionName) => (
            <section key={collectionName} className="space-y-4">
              <h2 className="text-xl font-bold text-white/90 border-b border-white/10 pb-2 uppercase tracking-widest text-sm flex items-center gap-3">
                <span className="w-2 h-2 rounded-full bg-sky-400 shadow-[0_0_8px_rgba(56,189,248,0.8)]"></span>
                {collectionName.replace(/_/g, ' ')}
                <span className="text-sky-400/50 bg-sky-400/10 px-2 py-0.5 rounded-full text-xs">
                  {memoryData[collectionName].length}
                </span>
              </h2>
              
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                {memoryData[collectionName].map((memory, idx) => (
                  <div 
                    key={memory?.id ?? memory?.memory_id ?? `memory-${idx}`}
                    className="p-5 rounded-2xl bg-white/[0.02] border border-white/10 hover:border-sky-400/30 hover:bg-white/[0.04] transition-all group relative overflow-hidden"
                  >
                    <div className="absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-transparent via-sky-400/20 to-transparent opacity-0 group-hover:opacity-100 transition-opacity"></div>
                    
                    <div className="flex flex-col h-full gap-3">
                      {Object.entries(memory).map(([key, value]) => {
                        // Skip empty fields or complex nested objects for a cleaner UI
                        if (!value || typeof value === 'object') return null
                        
                        return (
                          <div key={key} className="space-y-1">
                            <h4 className="text-[10px] font-bold text-white/40 uppercase tracking-widest">{key}</h4>
                            <p className="text-sm text-white/80 leading-relaxed font-medium">
                              {String(value)}
                            </p>
                          </div>
                        )
                      })}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          ))
        )}

      </div>
    </div>
  )
}
