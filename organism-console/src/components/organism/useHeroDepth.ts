import { useRef, useState, useEffect, useCallback } from "react"

export type HeroTilt = {
  rotateX: number
  rotateY: number
  driftX: number
  driftY: number
  glowX: number
  glowY: number
  surge: number
}

const IDLE: HeroTilt = {
  rotateX: 0,
  rotateY: 0,
  driftX: 0,
  driftY: 0,
  glowX: 50,
  glowY: 50,
  surge: 0
}

export function useHeroDepth() {
  const ref = useRef<HTMLElement | null>(null)
  const [tilt, setTilt] = useState<HeroTilt>(IDLE)
  const frameRef = useRef<number | null>(null)
  const currentRef = useRef<HeroTilt>(IDLE)

  const lerp = (a: number, b: number, t: number) => a + (b - a) * t

  const onMove = useCallback((e: MouseEvent) => {
    const el = ref.current
    if (!el) return

    const rect = el.getBoundingClientRect()
    const nx = (e.clientX - rect.left) / rect.width
    const ny = (e.clientY - rect.top) / rect.height
    const rx = (ny - 0.5) * -10
    const ry = (nx - 0.5) * 12
    const dist = Math.sqrt((nx - 0.5) ** 2 + (ny - 0.5) ** 2)

    const target: HeroTilt = {
      rotateX: rx,
      rotateY: ry,
      driftX: (nx - 0.5) * 24,
      driftY: (ny - 0.5) * 16,
      glowX: nx * 100,
      glowY: ny * 100,
      surge: Math.max(0, 1 - dist * 2.2)
    }

    if (frameRef.current !== null) cancelAnimationFrame(frameRef.current)

    const step = () => {
      currentRef.current = {
        rotateX: lerp(currentRef.current.rotateX, target.rotateX, 0.10),
        rotateY: lerp(currentRef.current.rotateY, target.rotateY, 0.10),
        driftX: lerp(currentRef.current.driftX, target.driftX, 0.10),
        driftY: lerp(currentRef.current.driftY, target.driftY, 0.10),
        glowX: lerp(currentRef.current.glowX, target.glowX, 0.08),
        glowY: lerp(currentRef.current.glowY, target.glowY, 0.08),
        surge: lerp(currentRef.current.surge, target.surge, 0.10)
      }

      setTilt({ ...currentRef.current })

      const maxDelta = Math.max(
        Math.abs(currentRef.current.rotateX - target.rotateX),
        Math.abs(currentRef.current.rotateY - target.rotateY),
        Math.abs(currentRef.current.driftX - target.driftX),
        Math.abs(currentRef.current.driftY - target.driftY)
      )

      if (maxDelta > 0.05) {
        frameRef.current = requestAnimationFrame(step)
      } else {
        currentRef.current = target
        setTilt({ ...target })
        frameRef.current = null
      }
    }

    frameRef.current = requestAnimationFrame(step)
  }, [])

  const onLeave = useCallback(() => {
    if (frameRef.current !== null) cancelAnimationFrame(frameRef.current)

    const step = () => {
      currentRef.current = {
        rotateX: lerp(currentRef.current.rotateX, 0, 0.08),
        rotateY: lerp(currentRef.current.rotateY, 0, 0.08),
        driftX: lerp(currentRef.current.driftX, 0, 0.08),
        driftY: lerp(currentRef.current.driftY, 0, 0.08),
        glowX: lerp(currentRef.current.glowX, 50, 0.06),
        glowY: lerp(currentRef.current.glowY, 50, 0.06),
        surge: lerp(currentRef.current.surge, 0, 0.08)
      }

      setTilt({ ...currentRef.current })

      const maxDelta = Math.max(
        Math.abs(currentRef.current.rotateX),
        Math.abs(currentRef.current.rotateY),
        Math.abs(currentRef.current.driftX),
        Math.abs(currentRef.current.driftY),
        Math.abs(currentRef.current.surge)
      )

      if (maxDelta > 0.04) {
        frameRef.current = requestAnimationFrame(step)
      } else {
        currentRef.current = IDLE
        setTilt(IDLE)
        frameRef.current = null
      }
    }

    frameRef.current = requestAnimationFrame(step)
  }, [])

  useEffect(() => {
    const el = ref.current
    if (!el) return

    el.addEventListener("mousemove", onMove)
    el.addEventListener("mouseleave", onLeave)

    return () => {
      el.removeEventListener("mousemove", onMove)
      el.removeEventListener("mouseleave", onLeave)
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current)
    }
  }, [onMove, onLeave])

  return { ref, tilt }
}
