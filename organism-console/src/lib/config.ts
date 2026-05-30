export const appConfig = {
  appName: "Organism Console",
  backendBaseUrl: "http://127.0.0.1:8000",
  endpoints: {
    health: "/health",
    ready: "/health",
    status: "/health",
    tools: "/traces/summary?limit=50",
    toolsCache: "/traces/summary?limit=50",
    traces: "/traces?limit=50",
    traceSummary: "/traces/summary?limit=50",
    adminStatus: "/health",
    adminDashboard: "/traces/summary?limit=50",
    adminGeneration: "/health"
  },
  requestTimeoutMs: 15000,
  sseEnabled: true
} as const
