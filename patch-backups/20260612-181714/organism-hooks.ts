import { useMemo } from "react"
import { useQuery } from "@tanstack/react-query"
import { api } from "../../lib/api"
import { useUiStore } from "../../state/ui-store"
import type { TracesResponse } from "../../lib/types"

import type {
  OrganismStatusResponse,
  OrganismToolsResponse,
  ToolsCacheResponse,
  TimelinePoint
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
    refetchInterval: 30000
  })

  const toolsQuery = useQuery({
    queryKey: ["organism-tools", backendUrl],
    queryFn: () => api.getTools<OrganismToolsResponse>(backendUrl),
    retry: 1,
    refetchInterval: 60000
  })

  const toolsCacheQuery = useQuery({
    queryKey: ["organism-tools-cache", backendUrl],
    queryFn: () => api.getToolsCache<ToolsCacheResponse>(backendUrl),
    retry: 1,
    refetchInterval: 60000
  })

  const timelineQuery = useQuery<TracesResponse, Error>({
    queryKey: ["organism-timeline", backendUrl],
    queryFn: () => api.getTraces(backendUrl),
    retry: 1,
    refetchInterval: 15000
  })

  const derived = useMemo(() => {
    const capabilities = toolsQuery.data?.capabilities ?? []
    const cacheSize = toolsCacheQuery.data?.cache_size ?? 0
    const cachedKeys = toolsCacheQuery.data?.cached_keys ?? []
    const eventCount = statusQuery.data?.event_count ?? 0
    const traceItems = timelineQuery.data?.traces ?? []
    const groupedBuckets = new Map<string, TimelinePoint>()

    for (const item of traceItems) {
      const date =
        typeof item.timestamp_ms === "number" && Number.isFinite(item.timestamp_ms)
          ? new Date(item.timestamp_ms)
          : null

      const bucket = date && !Number.isNaN(date.getTime())
        ? `${date.getHours().toString().padStart(2, "0")}:${date.getMinutes().toString().padStart(2, "0")}`
        : "unknown"

      const existing = groupedBuckets.get(bucket) ?? {
        bucket,
        event_count: 0,
        success_count: 0,
        partial_count: 0,
        fail_count: 0
      }

      existing.event_count += 1

      const status = String(item.status ?? "").toLowerCase()
      if (status.includes("success") || status.includes("ok") || status.includes("complete")) {
        existing.success_count += 1
      } else if (status.includes("partial") || status.includes("warn")) {
        existing.partial_count += 1
      } else {
        existing.fail_count += 1
      }

      groupedBuckets.set(bucket, existing)
    }

    const timelinePoints = Array.from(groupedBuckets.values()).sort((a, b) =>
      a.bucket.localeCompare(b.bucket)
    )
    const toolCount = toolsQuery.data?.count ?? 0
    const systemReady = statusQuery.data?.ready ?? false
    const ollamaReady = statusQuery.data?.ollama_reachable ?? false

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
      `ollama ${ollamaReady ? "reachable" : "offline"}`
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
        label: "Ollama",
        value: getStatusText(ollamaReady, "Reachable", "Offline"),
        accent: getStatusColor(ollamaReady),
        detail: ollamaReady
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

    return {
      capabilities,
      cacheSize,
      cachedKeys,
      eventCount,
      timelinePoints,
      toolCount,
      systemReady,
      ollamaReady,
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
      insights
    }
  }, [statusQuery.data, toolsQuery.data, toolsCacheQuery.data, timelineQuery.data])

  const isLoading =
    statusQuery.isLoading || toolsQuery.isLoading || toolsCacheQuery.isLoading || timelineQuery.isLoading

  const isError =
    statusQuery.isError || toolsQuery.isError || toolsCacheQuery.isError || timelineQuery.isError

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











