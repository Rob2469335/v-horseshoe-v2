import * as React from "react"
import { useUiStore } from "../../state/ui-store"
import { Button } from "./button"
import { X, Sparkles } from "lucide-react"

interface ActionOnboardingCardProps {
  id: string
  title: string
  description: React.ReactNode
  actionLabel: string
  onAction: () => void
  icon?: React.ReactNode
  hero?: boolean
}

export function ActionOnboardingCard({
  id,
  title,
  description,
  actionLabel,
  onAction,
  icon,
  hero = false,
}: ActionOnboardingCardProps) {
  const dismissed = useUiStore((state) => state.dismissedOnboarding[id])
  const dismiss = useUiStore((state) => state.dismissOnboarding)

  if (dismissed) return null

  return (
    <div
      className={`relative overflow-hidden rounded-xl border p-5 ${
        hero
          ? "border-violet-500/30 bg-gradient-to-br from-violet-500/10 to-transparent shadow-[0_0_20px_rgba(139,92,246,0.1)]"
          : "border-sky-500/20 bg-gradient-to-br from-sky-500/5 to-transparent"
      }`}
    >
      <div className="absolute right-0 top-0 h-32 w-32 -translate-y-1/2 translate-x-1/2 rounded-full bg-white/5 blur-3xl" />
      
      <button
        onClick={() => dismiss(id)}
        className="absolute right-3 top-3 text-white/40 transition-colors hover:text-white"
        aria-label="Dismiss"
      >
        <X size={16} />
      </button>

      <div className="relative z-10 flex items-start gap-4">
        <div
          className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border ${
            hero ? "border-violet-500/30 bg-violet-500/20 text-violet-300" : "border-sky-500/30 bg-sky-500/20 text-sky-300"
          }`}
        >
          {icon || <Sparkles size={20} />}
        </div>
        
        <div className="flex-1">
          <h3 className={`font-semibold ${hero ? "text-lg text-violet-100" : "text-base text-sky-100"}`}>
            {title}
          </h3>
          <p className="mt-1 text-sm text-white/60">{description}</p>
          
          <div className="mt-4">
            <Button
              onClick={() => {
                onAction()
              }}
              variant={hero ? "default" : "outline"}
              size="sm"
              className={hero ? "bg-violet-600 hover:bg-violet-500" : "border-sky-500/30 hover:bg-sky-500/10"}
            >
              {actionLabel}
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
