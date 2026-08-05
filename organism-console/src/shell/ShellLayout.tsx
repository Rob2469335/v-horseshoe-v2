import { Outlet, useLocation } from 'react-router-dom'
import Sidebar from './Sidebar'
import Topbar from './Topbar'
import { motion, AnimatePresence } from 'framer-motion'
import { SwarmTopology3D } from '../components/SwarmTopology3D'

export default function ShellLayout() {
  const location = useLocation()
  
  return (
    <div className="flex h-screen w-full bg-[#04080f] overflow-hidden text-[#f0f6fc] relative">
      <SwarmTopology3D />
      <Sidebar />
      <div className="flex flex-col flex-1 min-w-0">
        <Topbar />
        <main className="flex-1 min-w-0 overflow-y-auto p-4 md:p-8 bg-transparent">
          <AnimatePresence mode="wait">
            <motion.div
              key={location.pathname}
              initial={{ opacity: 0, y: 10, filter: "blur(5px)" }}
              animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
              exit={{ opacity: 0, y: -10, filter: "blur(5px)" }}
              transition={{ duration: 0.25, ease: "easeInOut" }}
              className="w-full h-full max-w-[1600px] mx-auto"
            >
              <Outlet />
            </motion.div>
          </AnimatePresence>
        </main>
      </div>
    </div>
  )
}
