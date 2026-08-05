import { useMemo } from "react";
import { getSubsystemTheme } from "./organism-theme";
import type { OrganismSubsystem } from "./organism-types";

interface PresenterProps {
  activeCard: OrganismSubsystem;
  interactionMode: "observe" | "teach" | "drill";
  selectedBucket: string | null;
  timelinePoints: any[];
  insights: any;
  systemReady: boolean;
  toolCount: number;
  visionRuntimeReady: boolean;
  statusQueryData: any;
  totalTimelineEvents: number;
  totalTimelineSuccess: number;
  capabilities: string[];
  cacheSize: number;
  cachedKeys: any[];
}

const COMPACT_FORMATTER = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });

export function formatCompact(value: number) {
  return COMPACT_FORMATTER.format(value);
}

export function useOrganismPresenter({
  activeCard,
  interactionMode,
  selectedBucket,
  timelinePoints,
  insights,
  systemReady,
  toolCount,
  visionRuntimeReady,
  statusQueryData,
  totalTimelineEvents,
  totalTimelineSuccess,
  capabilities,
  cacheSize,
  cachedKeys
}: PresenterProps) {
  const activeBucketData = useMemo(() => {
    if (!selectedBucket) return null;
    return timelinePoints.find((p) => p.bucket === selectedBucket) ?? null;
  }, [selectedBucket, timelinePoints]);

  const bucketComparison = useMemo(() => insights?.getComparison?.(selectedBucket), [selectedBucket, insights]);

  const activeTheme = getSubsystemTheme(activeCard);

  const activeMessage = useMemo(() => {
    if (activeBucketData) {
      return `Focused bucket ${activeBucketData.bucket} handled ${activeBucketData.event_count} events with ${activeBucketData.success_count} successes and ${activeBucketData.fail_count} failures.`;
    }

    switch (activeCard) {
      case "learning":
        return `Learning converts live events into memory traces. Throughput is currently ${insights?.volumeTrend || 'stable'}.`;
      case "healing":
        return `Healing watches resilience and recovery posture. The organism is ${systemReady ? "stable" : "recovering"}.`;
      case "autonomy":
        return `Autonomy measures action reach across tools and workflows. ${toolCount} tools are exposed to the organism.`;
      case "vision":
        return `Vision maps image-aware reasoning into runtime action. The multimodal path is ${visionRuntimeReady ? "live" : "pending"}.`;
      default:
        return `Operator control stays on top of every subsystem. ${insights?.action || ''}`;
    }
  }, [activeBucketData, activeCard, insights, systemReady, toolCount, visionRuntimeReady]);

  const tutorialContent = useMemo(() => {
    const base = (() => {
      switch (activeCard) {
        case "learning":
          return {
            steps: [
              "Watch raw events turn into reusable traces and memory structures.",
              "Compare throughput with success rate to see whether more activity is actually helping.",
              "Healthy learning means volume rises without collapse in outcome quality."
            ],
            operatorAction: insights?.summary || "",
            deepDive: [
              "Trace reuse improves speed because the organism can recall prior successful work.",
              "Cache growth usually means the organism is building reusable operational memory.",
              "If failures climb during higher throughput, training quality is lagging behind activity."
            ]
          };
        case "healing":
          return {
            steps: [
              "Start by checking if readiness is healthy before trusting advanced metrics.",
              "Review failure pressure and bucket drift to see whether the organism is compensating.",
              "A healthy self-heal loop contains local damage before it spreads system-wide."
            ],
            operatorAction: `Healing posture is ${systemReady ? "healthy" : "degraded"}. ${systemReady ? "The organism is stable enough for normal use." : "Proceed carefully while recovery is active."}`,
            deepDive: [
              "Self-heal starts as visibility, not magic. You need signals before you can automate repair.",
              "Failure pressure matters more than a single red flag because it shows sustained stress.",
              "Recovery loops are strongest when they are observable, reversible, and narrow in scope."
            ]
          };
        case "autonomy":
          return {
            steps: [
              "Inspect tool reach first. If the organism cannot act, intelligence stays theoretical.",
              "Compare capabilities with failure pressure to judge safe autonomy.",
              "More tools increase reach, but they also increase the surface area for mistakes."
            ],
            operatorAction: insights?.action || "",
            deepDive: [
              "Autonomy should follow a plan-execute-verify loop, not blind action.",
              "Capability exposure must stay human-steerable even as the system becomes more agentic.",
              "Reusable traces and cache hits make autonomous workflows faster and more predictable."
            ]
          };
        case "vision":
          return {
            steps: [
              "Vision becomes meaningful only when the runtime path is actually reachable.",
              "If vision is pending, multimodal flows will degrade into text-only behavior.",
              "Treat vision readiness as a dependency for screen, image, and spatial workflows."
            ],
            operatorAction: visionRuntimeReady
              ? "Vision is live. Image-aware behavior can participate in the organism."
              : "Vision is pending. Multimodal behavior is currently limited.",
            deepDive: [
              "Vision models turn pixel data into structured semantic tokens for higher-level reasoning.",
              "Grounding matters because perception without action is only observation.",
              `Installed vision models reported: ${statusQueryData?.vision_models_installed?.join(", ") || "none reported"}`
            ]
          };
        default:
          return {
            steps: [
              "Use Observe for scanning, Teach for explanations, and Drill for deep inspection.",
              "The operator layer keeps human-in-the-loop control over every autonomous subsystem.",
              "A strong console teaches what the organism is doing instead of hiding its logic."
            ],
            operatorAction: `Operator oversight is active for the ${activeCard} subsystem.`,
            deepDive: [
              "Human steerability should remain visible even in highly autonomous systems.",
              "The best dashboards do not just show numbers; they explain consequences.",
              "Live organism control works best when telemetry and interpretation stay connected."
            ]
          };
      }
    })();

    if (activeBucketData) {
      const bucketSuccessRate = Math.round((activeBucketData.success_count / Math.max(1, activeBucketData.event_count)) * 100);
      return {
        ...base,
        operatorAction: `Focused on bucket ${activeBucketData.bucket}. Success rate in this window is ${bucketSuccessRate}%. ${bucketSuccessRate < 70 ? "This bucket shows notable failure pressure." : "This bucket is operating within healthy bounds."}`
      };
    }

    return base;
  }, [activeCard, activeBucketData, insights, systemReady, visionRuntimeReady, statusQueryData]);

  const modeSummary = useMemo(() => {
    switch (interactionMode) {
      case "observe":
        return {
          title: "Observation posture",
          detail: "Fast scanning mode for operators who want the organism's live pulse without the full teaching overlay."
        };
      case "teach":
        return {
          title: "Tutor control mode",
          detail: "Plain-English explanations are active so every metric reads like guided instruction instead of raw telemetry."
        };
      default:
        return {
          title: "Deep inspection mode",
          detail: "Technical transparency is expanded so you can inspect the organism's mechanics, drift, and pressure in detail."
        };
    }
  }, [interactionMode]);

  const tryThisNext = useMemo(() => {
    if (selectedBucket) return "Click the timeline background to release bucket focus and return to live stream monitoring.";
    switch (activeCard) {
      case "learning":
        return "Drive more real traffic through the system and watch whether trace quality rises with volume.";
      case "healing":
        return "Test a controlled failure and confirm that the organism recovers without broad instability.";
      case "autonomy":
        return "Expand safe tool reach, then verify whether success holds under more autonomous action.";
      case "vision":
        return "Bring the vision runtime online and compare before-and-after capability posture.";
      default:
        return "Switch to Drill mode for deeper architectural explanation.";
    }
  }, [activeCard, selectedBucket]);

  const tutorMeta = useMemo(() => {
    const subsystemLabel = activeCard.charAt(0).toUpperCase() + activeCard.slice(1);
    return {
      eyebrow: `Subsystem focus: ${subsystemLabel}`,
      title: `${subsystemLabel} intelligence`,
      intro: `Direct teaching and control surface for the organism's ${activeCard} subsystem.`
    };
  }, [activeCard]);

  const overviewCards = useMemo(() => [
    {
      label: "Organism state",
      value: systemReady ? "ready" : "review",
      detail: systemReady
        ? "The main runtime is reporting healthy readiness."
        : "Core readiness is under pressure and needs review.",
      accent: systemReady ? "text-green-500" : "text-amber-500"
    },
    {
      label: "Timeline volume",
      value: formatCompact(totalTimelineEvents),
      detail: `${formatCompact(totalTimelineSuccess)} successful events across the tracked timeline.`,
      accent: "text-sky-400"
    },
    {
      label: "Autonomy reach",
      value: String(toolCount),
      detail: capabilities.length > 0
        ? `${Math.min(capabilities.length, 4)} visible capabilities include ${capabilities.slice(0, 4).join(", ")}`
        : "No capabilities are currently exposed.",
      accent: "text-purple-400"
    },
    {
      label: "Memory reuse",
      value: formatCompact(cacheSize),
      detail: cachedKeys.length > 0
        ? `${cachedKeys.length} cached keys are available for faster reuse.`
        : "No cached keys reported yet.",
      accent: "text-pink-400"
    }
  ], [systemReady, totalTimelineEvents, totalTimelineSuccess, toolCount, capabilities, cacheSize, cachedKeys]);

  return {
    activeBucketData,
    bucketComparison,
    activeTheme,
    activeMessage,
    tutorialContent,
    modeSummary,
    tryThisNext,
    tutorMeta,
    overviewCards
  };
}
