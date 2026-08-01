import { motion } from 'framer-motion'
import { useMemo, useEffect, useState } from 'react'

export function SwarmTopology3D() {
  const [mounted, setMounted] = useState(false)
  
  useEffect(() => {
    setMounted(true)
  }, [])

  const nodes = useMemo(() => {
    return Array.from({ length: 30 }).map((_, i) => ({
      id: i,
      x: Math.random() * 100,
      y: Math.random() * 100,
      size: Math.random() * 4 + 2,
      duration: Math.random() * 10 + 10,
    }))
  }, [])

  const lines = useMemo(() => {
    const l = []
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const dx = nodes[i].x - nodes[j].x
        const dy = nodes[i].y - nodes[j].y
        // Connect nearby nodes
        if (Math.sqrt(dx * dx + dy * dy) < 20) {
          l.push([nodes[i], nodes[j]])
        }
      }
    }
    return l
  }, [nodes])

  if (!mounted) return null

  return (
    <div className="absolute inset-0 z-0 pointer-events-none opacity-40 overflow-hidden bg-[#04080f]">
      <svg className="absolute inset-0 w-full h-full" preserveAspectRatio="none">
        {lines.map((pts, i) => (
          <motion.line
            key={i}
            x1={`${pts[0].x}%`}
            y1={`${pts[0].y}%`}
            x2={`${pts[1].x}%`}
            y2={`${pts[1].y}%`}
            stroke="#0891b2"
            strokeWidth="0.5"
            strokeOpacity="0.3"
            initial={{ pathLength: 0, opacity: 0 }}
            animate={{ pathLength: 1, opacity: 0.3 }}
            transition={{ duration: 2, ease: "easeOut" }}
          />
        ))}
      </svg>
      {nodes.map((node) => (
        <motion.div
          key={node.id}
          className="absolute rounded-full bg-cyan-400 shadow-[0_0_10px_2px_rgba(34,211,238,0.5)]"
          style={{
            width: node.size,
            height: node.size,
            left: `${node.x}%`,
            top: `${node.y}%`,
          }}
          animate={{
            y: [0, -20, 0],
            x: [0, 10, 0],
            opacity: [0.3, 0.8, 0.3]
          }}
          transition={{
            duration: node.duration,
            repeat: Infinity,
            ease: "easeInOut"
          }}
        />
      ))}
      {/* Heavy vignette for deep space vibe */}
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,_var(--tw-gradient-stops))] from-transparent via-[#04080f]/50 to-[#04080f]/95" />
    </div>
  )
}
