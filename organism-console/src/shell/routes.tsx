import App from "../App"
import { createBrowserRouter } from "react-router-dom"
import ShellLayout from "./ShellLayout"
import AgentPage from "../pages/AgentPage"
import WorkspacePage from "../pages/WorkspacePage"
import OrganismPage from "../pages/OrganismPage"
import MemorySearchPage from "../pages/MemorySearchPage"
import OpsPage from "../pages/OpsPage"
import IntegrationsPage from "../pages/IntegrationsPage"
import LearnedMemoriesPage from "../pages/LearnedMemoriesPage"
import CommandCenterPage from "../pages/CommandCenterPage"
import type { NavItem } from "../lib/types"

export const appRoutes: NavItem[] = [
  { key: "command", label: "Command Center", path: "/command", description: "Control the whole machine: probes, healing, screen, models" },
  { key: "agent", label: "Agent", path: "/agent", description: "Agent console and execution" },
  { key: "workspace", label: "Workspace", path: "/workspace", description: "Workspace context and files" },
  { key: "organism", label: "Organism", path: "/organism", description: "Swarm and organism view" },
  { key: "memory-search", label: "Memory/Search", path: "/memory-search", description: "Memory and retrieval" },
  { key: "memories", label: "Learned Memories", path: "/memories", description: "Offline learned memories" },
  { key: "ops", label: "Ops", path: "/ops", description: "Backend operations and health" },
  { key: "integrations", label: "Integrations", path: "/integrations", description: "External systems and providers" }
]

export const router = createBrowserRouter([
  {
    path: "/",
    element: <App />,
    children: [
      {
        path: "/",
        element: <ShellLayout />,
        children: [
          { index: true, element: <OpsPage /> },
          { path: "command", element: <CommandCenterPage /> },
          { path: "agent", element: <AgentPage /> },
          { path: "workspace", element: <WorkspacePage /> },
          { path: "organism", element: <OrganismPage /> },
          { path: "memory-search", element: <MemorySearchPage /> },
          { path: "memories", element: <LearnedMemoriesPage /> },
          { path: "ops", element: <OpsPage /> },
          { path: "integrations", element: <IntegrationsPage /> }
        ]
      }
    ]
  }
])
