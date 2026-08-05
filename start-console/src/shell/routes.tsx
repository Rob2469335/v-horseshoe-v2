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
