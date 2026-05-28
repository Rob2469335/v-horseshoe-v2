import { create } from "zustand"
import type { PanelKey } from "../lib/types"

type ConnectionStatus = "idle" | "connecting" | "online" | "degraded" | "offline"

interface UiState {
  activePanel: PanelKey
  sidebarCollapsed: boolean
  selectedWorkspace: string
  backendUrl: string
  connectionStatus: ConnectionStatus
  setActivePanel: (panel: PanelKey) => void
  setSidebarCollapsed: (collapsed: boolean) => void
  setSelectedWorkspace: (workspace: string) => void
  setBackendUrl: (url: string) => void
  setConnectionStatus: (status: ConnectionStatus) => void
}

export const useUiStore = create<UiState>()((set) => ({
  activePanel: "ops",
  sidebarCollapsed: false,
  selectedWorkspace: "v-horseshoe-v2",
  backendUrl: "http://127.0.0.1:8000",
  connectionStatus: "idle",
  setActivePanel: (panel) => set({ activePanel: panel }),
  setSidebarCollapsed: (collapsed) => set({ sidebarCollapsed: collapsed }),
  setSelectedWorkspace: (workspace) => set({ selectedWorkspace: workspace }),
  setBackendUrl: (url) => set({ backendUrl: url }),
  setConnectionStatus: (status) => set({ connectionStatus: status })
}))
