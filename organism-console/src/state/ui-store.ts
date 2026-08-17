import { create } from "zustand"
import { persist } from "zustand/middleware"
import type { PanelKey } from "../lib/types"

type ConnectionStatus = "idle" | "unknown" | "connecting" | "online" | "degraded" | "offline"

interface UiState {
  activePanel: PanelKey
  sidebarCollapsed: boolean
  selectedWorkspace: string
  backendUrl: string
  connectionStatus: ConnectionStatus
  dismissedOnboarding: Record<string, boolean>
  setActivePanel: (panel: PanelKey) => void
  setSidebarCollapsed: (collapsed: boolean) => void
  toggleSidebar: () => void
  setSelectedWorkspace: (workspace: string) => void
  setBackendUrl: (url: string) => void
  setConnectionStatus: (status: ConnectionStatus) => void
  dismissOnboarding: (id: string) => void
}

export const useUiStore = create<UiState>()(
  persist(
    (set) => ({
      activePanel: "ops",
      sidebarCollapsed: false,
      selectedWorkspace: "v-horseshoe-v2",
      backendUrl: "http://127.0.0.1:8000",
      connectionStatus: "idle",
      dismissedOnboarding: {},
      setActivePanel: (panel) => set({ activePanel: panel }),
      setSidebarCollapsed: (collapsed) => set({ sidebarCollapsed: collapsed }),
      toggleSidebar: () => set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
      setSelectedWorkspace: (workspace) => set({ selectedWorkspace: workspace }),
      setBackendUrl: (url) => set({ backendUrl: url }),
      setConnectionStatus: (status) => set({ connectionStatus: status }),
      dismissOnboarding: (id) => set((state) => ({ dismissedOnboarding: { ...state.dismissedOnboarding, [id]: true } }))
    }),
    {
      name: "ui-storage",
      partialize: (state) => ({ 
        sidebarCollapsed: state.sidebarCollapsed,
        backendUrl: state.backendUrl,
        selectedWorkspace: state.selectedWorkspace,
        dismissedOnboarding: state.dismissedOnboarding
      }),
    }
  )
)
