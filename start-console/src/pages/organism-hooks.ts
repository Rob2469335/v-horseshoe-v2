import { useQuery } from "@tanstack/react-query"
import { api } from "../lib/api"
import type { StatusResponse, ToolsResponse, TraceSummaryResponse } from "../lib/types"
import { useUiStore } from "../state/ui-store"

export function useOrganismData() {
  const backendUrl = useUiStore((state) => state.backendUrl)

  const statusQuery = useQuery({
    queryKey: ["organism-status", backendUrl],
    queryFn: () => api.getStatus<StatusResponse>(backendUrl),
    retry: 1,
    staleTime: 30000,
  })

  const toolsQuery = useQuery({
    queryKey: ["organism-tools", backendUrl],
    queryFn: () => api.getTools<ToolsResponse>(backendUrl),
    retry: 1,
    staleTime: 60000,
  })

  const traceSummaryQuery = useQuery({
    queryKey: ["organism-trace-summary", backendUrl],
    queryFn: () => api.getTraceSummary(backendUrl),
    retry: false,
    staleTime: 30000,
  })

  const isLoading =
    statusQuery.isLoading || toolsQuery.isLoading || traceSummaryQuery.isLoading

  const isError = statusQuery.isError || toolsQuery.isError

  const systemReady = statusQuery.data?.ready ?? false
  const toolCount = toolsQuery.data?.tools?.length ?? toolsQuery.data?.count ?? 0
  const traceSummaryItems: TraceSummaryResponse = traceSummaryQuery.data ?? []

  return {
    backendUrl,
    isLoading,
    isError,
    statusQuery,
    toolsQuery,
    traceSummaryQuery,
    systemReady,
    toolCount,
    traceSummaryItems,
    traceCount: traceSummaryItems.length,
  }
}
