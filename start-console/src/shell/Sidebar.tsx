import { Link, useRouterState } from '@tanstack/react-router'
import { appRoutes } from './routes'
import { useUiStore } from '../state/ui-store'
import { motion } from 'framer-motion'

export default function Sidebar() {
  const sidebarCollapsed = useUiStore((state) => state.sidebarCollapsed)
  const routerState = useRouterState()
  const pathname = routerState.location.pathname

  return (
    <motion.aside 
      animate={{ width: sidebarCollapsed ? 88 : 260 }}
      transition={{ type: "spring", stiffness: 300, damping: 30 }}
      className="h-full border-r border-white/10 bg-[#0d121d]/95 backdrop-blur-xl p-6 shrink-0 overflow-hidden flex flex-col"
    >
      <div className="flex items-center h-10 mb-6 px-3 border border-sky-400 rounded-xl font-black text-sky-400 bg-sky-400/10 shadow-[0_0_20px_rgba(56,189,248,0.15)] uppercase tracking-widest shrink-0 justify-center">
        {sidebarCollapsed ? 'OC' : 'Organism Console'}
      </div>
      <nav className="flex flex-col gap-2 relative flex-1">
        {appRoutes.map((route) => {
          const isActive = pathname === route.path || (route.path !== '/' && pathname.startsWith(route.path))
          return (
            <Link
              key={route.path}
              to={route.path}
              className="relative flex items-center min-h-[44px] px-4 rounded-xl text-white/60 hover:text-white transition-colors z-10"
            >
              {isActive && (
                <motion.div 
                  layoutId="sidebar-active"
                  className="absolute inset-0 bg-sky-400/10 border border-sky-400 rounded-xl shadow-[inset_4px_0_0_-2px_rgba(56,189,248,1)] z-[-1]"
                  initial={false}
                  transition={{ type: "spring", stiffness: 400, damping: 30 }}
                />
              )}
              <span className={`font-semibold tracking-wide truncate ${isActive ? 'text-white' : ''}`}>
                {sidebarCollapsed ? route.label.charAt(0) : route.label}
              </span>
            </Link>
          )
        })}
      </nav>
    </motion.aside>
  )
}
