import StatusBadge from '../components/StatusBadge'
import { useUiStore } from '../state/ui-store'
import { motion } from 'framer-motion'

export default function Topbar() {
  const selectedWorkspace = useUiStore((state) => state.selectedWorkspace)
  const backendUrl = useUiStore((state) => state.backendUrl)
  const connectionStatus = useUiStore((state) => state.connectionStatus)
  const toggleSidebar = useUiStore((state) => state.toggleSidebar)
  const setBackendUrl = useUiStore((state) => state.setBackendUrl)
  const setConnectionStatus = useUiStore((state) => state.setConnectionStatus)

  const tone =
    connectionStatus === 'online'
      ? 'success'
      : connectionStatus === 'offline'
      ? 'danger'
      : connectionStatus === 'connecting'
      ? 'warning'
      : 'neutral'

  return (
    <header className="flex items-center justify-between gap-4 px-8 h-[72px] border-b border-white/10 bg-[#0d121d]/85 backdrop-blur-md shrink-0">
      <div className="flex items-center gap-4 min-w-0">
        <motion.button 
          whileHover={{ scale: 1.05 }}
          whileTap={{ scale: 0.95 }}
          className="h-10 px-4 border border-white/10 rounded-xl text-white bg-[#0d121d] hover:border-sky-400 hover:shadow-[0_0_12px_rgba(56,189,248,0.2)] font-semibold transition-colors cursor-pointer" 
          type="button" 
          onClick={toggleSidebar}
        >
          Toggle
        </motion.button>
        <div className="flex flex-col gap-0.5">
          <span className="text-[11px] text-sky-400/80 font-extrabold uppercase tracking-widest">Workspace</span>
          <strong className="text-white truncate">{selectedWorkspace}</strong>
        </div>
      </div>

      <div className="flex items-center gap-3 min-w-0">
        <label className="flex flex-col gap-0.5 min-w-[240px] md:min-w-[320px]">
          <span className="text-[11px] text-sky-400/80 font-extrabold uppercase tracking-widest">Backend</span>
          <input
            className="h-10 px-3 border border-white/10 rounded-xl text-white bg-[#0d121d] focus:border-sky-400 focus:shadow-[0_0_12px_rgba(56,189,248,0.2)] focus:outline-none transition-all"
            type="text"
            value={backendUrl}
            onChange={(event) => {
              setBackendUrl(event.target.value)
              setConnectionStatus('unknown')
            }}
          />
        </label>
        <StatusBadge label={connectionStatus} tone={tone} />
        
        <motion.button 
          whileHover={{ scale: 1.05 }}
          whileTap={{ scale: 0.95 }}
          className="h-10 px-4 border border-white/10 rounded-xl text-white bg-[#0d121d] hover:border-sky-400 hover:shadow-[0_0_12px_rgba(56,189,248,0.2)] font-semibold transition-colors cursor-pointer" 
          type="button" 
          onClick={() => setConnectionStatus('connecting')}
        >
          Reconnect
        </motion.button>
      </div>
    </header>
  )
}
