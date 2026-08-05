import { useQuery } from "@tanstack/react-query"
import { api } from "../lib/api"
import type { StatusResponse, ToolsResponse, TraceSummaryResponse } from "../lib/types"
import { useUiStore } from "../state/ui-store"

/**
 * Aggregates the core data queries used by OrganismPage.
 *
 * Design contract:
 *  - statusQuery and toolsQuery are **required** — their failure
 *    sets isError and the page shows a top-level error boundary.
 *  - traceSummaryQuery is treated as **optional** telemetry; its failure is
 *    surfaced via traceSummaryQuery.isError but does NOT flip isError for the
 *    whole hook so the rest of the shell still renders.
 */
export function useOrganismData() {
  const backendUrl = useUiStore((state) => state.backendUrl)

  const statusQuery = useQuery({
    queryKey: ["organism-status", backendUrl],
    queryFn: () => api.getStatus<StatusResponse>(backendUrl),
    retry: 1,
    staleTime: 30_000,
  })

  const toolsQuery = useQuery({
    queryKey: ["organism-tools", backendUrl],
    queryFn: () => api.getTools<ToolsResponse>(backendUrl),
    retry: 1,
    staleTime: 60_000,
  })

  // Optional telemetry — failure must not collapse the whole page
  const traceSummaryQuery = useQuery({
    queryKey: ["organism-trace-summary", backendUrl],
    queryFn: () => api.getTraceSummary(backendUrl),
    retry: false,
    staleTime: 30_000,
  })

  const isLoading =
    statusQuery.isLoading || toolsQuery.isLoading || traceSummaryQuery.isLoading

  // Only core queries count toward isError — traceSummary is optional
  const isError = statusQuery.isError || toolsQuery.isError

  const systemReady = statusQuery.data?.ready ?? false
  const toolCount = toolsQuery.data?.tools?.length ?? toolsQuery.data?.count ?? 0
  const traceSummaryItems: TraceSummaryResponse = traceSummaryQuery.data ?? []

  return {
    backendUrl,
    isLoading,
    isError,

    // Per-query handles (let callers render granular error UI)
    statusQuery,
    toolsQuery,
    traceSummaryQuery,

    // Derived values
    systemReady,
    toolCount,
    traceSummaryItems,
    traceCount: traceSummaryItems.length,
  }
}
