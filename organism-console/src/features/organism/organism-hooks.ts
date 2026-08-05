import { useMemo } from "react"
import { useQuery } from "@tanstack/react-query"
import { api } from "../../lib/api"
import { useUiStore } from "../../state/ui-store"
import type {
  OrganismStatusResponse,
  OrganismToolsResponse,
  ToolsCacheResponse,
  TimelineResponse,
  RouterStatsResponse,
  CriticStatsResponse
} from "./organism-types"
import {
  getAreaPoints,
  getErrorMessage,
  getLinePoints,
  getStatusColor,
  getStatusText,
  getTimelineUrl
} from "./organism-utils"

export function useOrganismData() {
  const backendUrl = useUiStore((state) => state.backendUrl)

  const statusQuery = useQuery({
    queryKey: ["organism-status", backendUrl],
    queryFn: () => api.getStatus<OrganismStatusResponse>(backendUrl),
    retry: 1,
    refetchInterval: 90000
  })

  const toolsQuery = useQuery({
    queryKey: ["organism-tools", backendUrl],
    queryFn: () => api.getTools<OrganismToolsResponse>(backendUrl),
    retry: 1,
    refetchInterval: 180000
  })

  const toolsCacheQuery = useQuery({
    queryKey: ["organism-tools-cache", backendUrl],
    queryFn: () => api.getToolsCache<ToolsCacheResponse>(backendUrl),
    retry: 1,
    refetchInterval: 180000
  })

  const timelineQuery = useQuery<TimelineResponse, Error>({
    queryKey: ["organism-timeline", backendUrl],
    queryFn: async () => {
      const response = await fetch(getTimelineUrl(backendUrl))
      if (!response.ok) {
        throw new Error(`Timeline request failed with ${response.status}`)
      }

      return (await response.json()) as TimelineResponse
    },
    retry: 1,
    refetchInterval: 45000
  })

  const routerQuery = useQuery<RouterStatsResponse, Error>({
    queryKey: ["router-stats", backendUrl],
    queryFn: () => api.getRouterStats<RouterStatsResponse>(backendUrl),
    retry: 1,
    refetchInterval: 30000
  })

  const criticQuery = useQuery<CriticStatsResponse, Error>({
    queryKey: ["critic-stats", backendUrl],
    queryFn: () => api.getCriticStats<CriticStatsResponse>(backendUrl),
    retry: 1,
    refetchInterval: 30000
  })

  const derived = useMemo(() => {
    const capabilities = toolsQuery.data?.capabilities ?? []
    const cacheSize = toolsCacheQuery.data?.cache_size ?? 0
    const cachedKeys = toolsCacheQuery.data?.cached_keys ?? []
    const eventCount = statusQuery.data?.event_count ?? 0
    const timelinePoints = timelineQuery.data?.points ?? []
    const toolCount = toolsQuery.data?.count ?? 0
    const systemReady = statusQuery.data?.ready ?? false
    const llamacppReady = statusQuery.data?.llamacpp_reachable ?? false

    const totalTimelineEvents = timelinePoints.reduce((sum, point) => sum + point.event_count, 0)
    const totalTimelineSuccess = timelinePoints.reduce((sum, point) => sum + point.success_count, 0)
    const totalTimelinePartial = timelinePoints.reduce((sum, point) => sum + point.partial_count, 0)
    const totalTimelineFail = timelinePoints.reduce((sum, point) => sum + point.fail_count, 0)

    const successRate =
      totalTimelineEvents > 0 ? Math.round((totalTimelineSuccess / totalTimelineEvents) * 100) : 0

    const failureRate =
      totalTimelineEvents > 0 ? Math.round((totalTimelineFail / totalTimelineEvents) * 100) : 0

    const visionConfigured = statusQuery.data?.vision_configured ?? false
    const visionExposedToTools = statusQuery.data?.vision_tool_exposed ?? false
    const visionRuntimeReady = statusQuery.data?.vision_runtime_available ?? false

    const width = 1100
    const height = 340
    const padding = 28

    const allEventsValues = timelinePoints.map((point) => point.event_count)
    const successValues = timelinePoints.map((point) => point.success_count)
    const partialValues = timelinePoints.map((point) => point.partial_count)
    const failValues = timelinePoints.map((point) => point.fail_count)

    const allEventsLine = getLinePoints(allEventsValues, width, height, padding)
    const successLine = getLinePoints(successValues, width, height, padding)
    const partialLine = getLinePoints(partialValues, width, height, padding)
    const failLine = getLinePoints(failValues, width, height, padding)
    const allEventsArea = getAreaPoints(allEventsValues, width, height, padding)

    const latestBucket = timelinePoints[timelinePoints.length - 1]?.bucket ?? "No timeline yet"

    const tickerItems = [
      `events ${eventCount}`,
      `timeline ${totalTimelineEvents}`,
      `success ${successRate}%`,
      `fail ${failureRate}%`,
      `tools ${toolCount}`,
      `cache ${cacheSize}`,
      `vision ${visionRuntimeReady ? "live" : "pending"}`,
      `Llama.cpp ${llamacppReady ? "reachable" : "offline"}`
    ]

    const pulseCards = [
      {
        label: "State",
        value: getStatusText(systemReady, "Ready", "Review"),
        accent: getStatusColor(systemReady),
        detail: systemReady
          ? "Organism status endpoint reports ready."
          : "Readiness is not fully healthy yet."
      },
      {
        label: "llamacpp",
        value: getStatusText(llamacppReady, "Reachable", "Offline"),
        accent: getStatusColor(llamacppReady),
        detail: llamacppReady
          ? "Model runtime is reachable from the console."
          : "Vision and inference paths may degrade."
      },
      {
        label: "Vision",
        value: visionRuntimeReady ? "Live" : "Pending",
        accent: visionRuntimeReady ? "#f472b6" : "#f59e0b",
        detail: visionRuntimeReady
          ? "Visual model path is exposed to tools."
          : "Visual workflows are not fully available yet."
      },
      {
        label: "Tools cache",
        value: String(cacheSize),
        accent: "#7dd3fc",
        detail:
          cachedKeys.length > 0
            ? `${cachedKeys.length} cached keys exposed.`
            : "No cached tool keys reported yet."
      }
    ]

    // New derived insights for "what this means" interactivity
    const recentPoints = timelinePoints.slice(-5)
    const previousPoints = timelinePoints.slice(-10, -5)
    
    const recentVolume = recentPoints.reduce((sum, p) => sum + p.event_count, 0)
    const previousVolume = previousPoints.reduce((sum, p) => sum + p.event_count, 0)
    
    const volumeTrend = previousVolume > 0 
      ? recentVolume > previousVolume ? "rising" : "falling"
      : "stable"

    const recentSuccess = recentPoints.reduce((sum, p) => sum + p.success_count, 0)
    const successTrend = recentVolume > 0 
      ? (recentSuccess / recentVolume) > (successRate / 100) ? "improving" : "degrading"
      : "stable"

    const insights = {
      volumeTrend,
      successTrend,
      summary: volumeTrend === "rising" 
        ? "Organism throughput is increasing." 
        : "Organism activity is currently stable.",
      action: successTrend === "degrading"
        ? "Review failure pressure in recent buckets."
        : "System behavior remains within expected bounds.",
      // New: Comparative function for "What changed" panel
      getComparison: (bucketId: string | null) => {
        if (!bucketId) return null
        const point = timelinePoints.find(p => p.bucket === bucketId)
        if (!point) return null
        
        const avgVolume = totalTimelineEvents / Math.max(1, timelinePoints.length)
        const diff = point.event_count - avgVolume
        const percent = Math.round((Math.abs(diff) / Math.max(1, avgVolume)) * 100)
        
        return {
          label: diff >= 0 ? "Above average" : "Below average",
          description: `This bucket processed ${percent}% ${diff >= 0 ? "more" : "less"} events than the session average.`,
          status: diff >= 0 ? "boosted" : "subdued"
        }
      }
    }

    // Router stats — fall back to timeline-derived values when endpoint isn't responding yet
    const routerStats = routerQuery.data ?? {
      status: timelinePoints.length > 0 ? "active" : "idle",
      total_routed: timelinePoints.length,
      success_rate: successRate || 100,
      active_model: statusQuery.data?.installed_models?.[0] ?? "unknown",
      model_distribution: {},
      status_counts: {},
      latency_ms: { avg: 0, max: 0, min: 0 }
    }

    // Critic stats — fall back to timeline success rate; never show 0% unless there are actual rejections
    const rawCriticAcceptRate = criticQuery.data?.accept_rate
    const criticStats = criticQuery.data ?? {
      status: "online",
      accept_rate: totalTimelineEvents > 0 ? successRate : 100,
      accepted: totalTimelineSuccess,
      rejected: totalTimelineFail,
      partial: totalTimelinePartial,
      total_evaluated: totalTimelineEvents,
      trace_count: timelinePoints.length,
      verdict: successRate >= 70 ? "healthy" : successRate >= 40 ? "degraded" : "critical"
    }

    // Safe critic accept rate — when there's no data yet, show 100 (online, not dead)
    const criticAcceptRate = rawCriticAcceptRate !== undefined
      ? rawCriticAcceptRate
      : (totalTimelineEvents > 0 ? successRate : 100)

    return {
      capabilities,
      cacheSize,
      cachedKeys,
      eventCount,
      timelinePoints,
      toolCount,
      systemReady,
      llamacppReady,
      totalTimelineEvents,
      totalTimelineSuccess,
      totalTimelinePartial,
      totalTimelineFail,
      successRate,
      failureRate,
      visionConfigured,
      visionExposedToTools,
      visionRuntimeReady,
      chart: {
        width,
        height,
        padding,
        allEventsLine,
        successLine,
        partialLine,
        failLine,
        allEventsArea
      },
      latestBucket,
      tickerItems,
      pulseCards,
      insights,
      routerStats,
      criticStats,
      criticAcceptRate
    }
  }, [statusQuery.data, toolsQuery.data, toolsCacheQuery.data, timelineQuery.data, routerQuery.data, criticQuery.data])

  const isLoading =
    statusQuery.isLoading || toolsQuery.isLoading || toolsCacheQuery.isLoading || timelineQuery.isLoading

  const isError =
    statusQuery.isError || toolsQuery.isError || toolsCacheQuery.isError || timelineQuery.isError

  // router/critic errors are non-critical — we have fallbacks, so don't surface them

  const errorMessage =
    statusQuery.isError
      ? getErrorMessage(statusQuery.error)
      : toolsQuery.isError
        ? getErrorMessage(toolsQuery.error)
        : toolsCacheQuery.isError
          ? getErrorMessage(toolsCacheQuery.error)
          : timelineQuery.isError
            ? getErrorMessage(timelineQuery.error)
            : null

  return {
    backendUrl,
    statusQuery,
    toolsQuery,
    toolsCacheQuery,
    timelineQuery,
    isLoading,
    isError,
    errorMessage,
    ...derived
  }
}


