export const appConfig = {
  appName: "Organism Console",
  backendBaseUrl: "http://127.0.0.1:8000",
  endpoints: {
    health: "/health",
    ready: "/readyz",
    status: "/status",
    tools: "/tools",
    toolsCache: "/tools/cache",
    adminStatus: "/api/admin/status",
    adminDashboard: "/api/admin/dashboard",
    adminGeneration: "/api/admin/generation"
  },
  requestTimeoutMs: 15000,
  sseEnabled: true
} as const
