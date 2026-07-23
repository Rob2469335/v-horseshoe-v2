import { useState } from "react";
import { OrganismHero } from "../components/organism/OrganismHero";
import { OrganismAnatomySection } from "../components/organism/OrganismAnatomySection";
import { OrganismTimelineSection } from "../components/organism/OrganismTimelineSection";
import { OrganismTutorSection } from "../components/organism/OrganismTutorSection";
import { useOrganismData } from "../features/organism/organism-hooks";
import { LivingNervousSystem } from "../components/organism/LivingNervousSystem";
import { OrganismNarrator } from "../components/organism/OrganismNarrator";
import { SubsystemInteractiveCard } from "../components/organism/SubsystemInteractiveCard";
import { HealingTrigger } from "../components/organism/HealingTrigger";
import { GenerationHistory } from "../components/organism/GenerationHistory";
import { GenomesSection } from "../components/organism/GenomesSection";
import { ModelPicker } from "../components/organism/ModelPicker";
import { AgentStepRunner } from "../components/organism/AgentStepRunner";
import { MemorySearchPanel } from "../components/MemorySearchPanel";
import { ReplayDashboard } from "../components/ReplayDashboard";
import { OmniDevInterface } from "../components/organism/OmniDevInterface";
import { DebateRoomPanel } from "../components/organism/DebateRoomPanel";
import { SelfHealingPosturePanel } from "../components/organism/SelfHealingPosturePanel";
import { SwarmDashboard2027 } from "../components/organism/SwarmDashboard2027";
import { organismTheme } from "../features/organism/organism-theme";
import type { OrganismSubsystem } from "../features/organism/organism-types";

import { useSwarmStream } from "../features/swarm/useSwarmStream";
import { useOrganismPresenter, formatCompact } from "../features/organism/useOrganismPresenter";
import { OrganismCockpit } from "../components/organism/OrganismCockpit";
import { OrganismTutorial } from "../components/organism/OrganismTutorial";

export default function OrganismPage() {
  const {
    backendUrl,
    statusQuery,
    isLoading,
    isError,
    errorMessage,
    capabilities,
    cacheSize,
    cachedKeys,
    eventCount,
    toolCount,
    systemReady,
    totalTimelineEvents,
    totalTimelineSuccess,
    totalTimelinePartial,
    totalTimelineFail,
    successRate,
    failureRate,
    visionConfigured,
    visionRuntimeReady,
    timelinePoints,
    latestBucket,
    tickerItems,
    pulseCards,
    chart,
    insights,
    routerStats,
    criticStats,
    criticAcceptRate
  } = useOrganismData();

  const { width, height, padding, allEventsLine, successLine, partialLine, failLine, allEventsArea } = chart;
  const { swarmV10Feed, swarmCockpit } = useSwarmStream(backendUrl);

  const [activeCard, setActiveCard] = useState<OrganismSubsystem>("learning");
  const [showDeepTutorial, setShowDeepTutorial] = useState(false);
  const [interactionMode, setInteractionMode] = useState<"observe" | "teach" | "drill">("teach");
  const [selectedBucket, setSelectedBucket] = useState<string | null>(null);
  const [selectedModel, setSelectedModel] = useState<string | null>(null);

  const presenter = useOrganismPresenter({
    activeCard,
    interactionMode,
    selectedBucket,
    timelinePoints,
    insights,
    systemReady,
    toolCount,
    visionRuntimeReady,
    statusQueryData: statusQuery.data,
    totalTimelineEvents,
    totalTimelineSuccess,
    capabilities,
    cacheSize,
    cachedKeys
  });

  return (
    <>
      <OrganismCockpit stats={swarmCockpit} />
      <SwarmDashboard2027 backendUrl={backendUrl} />

      <main
        className="page min-h-screen pt-6 pb-12 px-4 transition-colors duration-500 bg-gradient-to-b from-[#040816] via-[#07101f] to-[#050815] text-[#ecf3ff] relative overflow-hidden"
        style={{ "--theme-accent": presenter.activeTheme.accent, "--theme-glow": presenter.activeTheme.glow } as React.CSSProperties}
      >
        <div className="absolute top-0 left-0 w-[45%] h-[500px] bg-[var(--theme-accent)] opacity-[0.11] blur-[140px] pointer-events-none rounded-full" />
        <div className="absolute top-0 right-0 w-[40%] h-[400px] bg-purple-500 opacity-[0.16] blur-[120px] pointer-events-none rounded-full" />
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[50%] h-[500px] bg-[var(--theme-glow)] opacity-[0.08] blur-[100px] pointer-events-none rounded-full" />
        
        <div className="max-w-[1440px] mx-auto grid gap-5 relative z-10">
          <OrganismTutorial
            modeSummary={presenter.modeSummary}
            activeMessage={presenter.activeMessage}
            interactionMode={interactionMode}
            setInteractionMode={(mode) => {
              setInteractionMode(mode);
              setShowDeepTutorial(mode === "drill");
            }}
            selectedBucket={selectedBucket}
            activeTheme={presenter.activeTheme}
            backendUrl={backendUrl}
            successRate={successRate}
            failureRate={failureRate}
            latestBucket={latestBucket}
          />

          <div className="grid grid-cols-[repeat(auto-fit,minmax(240px,1fr))] gap-4">
            {presenter.overviewCards.map((card) => (
              <article
                key={card.label}
                className="rounded-3xl p-5 bg-white/5 border border-white/10 shadow-[0_14px_28px_rgba(0,0,0,0.18)]"
              >
                <h3 className={`text-[11px] font-black uppercase tracking-[0.14em] mb-2 m-0 ${card.accent}`}>
                  {card.label}
                </h3>
                <div className="text-white text-[26px] font-black mb-2">
                  {card.value}
                </div>
                <div className="text-blue-50/70 text-sm leading-relaxed">
                  {card.detail}
                </div>
              </article>
            ))}
          </div>

          <article className="rounded-[26px] p-5 bg-gradient-to-b from-white/[0.035] to-white/2 border border-white/10">
            <div className="grid grid-cols-[repeat(auto-fit,minmax(260px,1fr))] gap-3.5">
              <div className="rounded-[18px] p-[18px] bg-white/[0.028] border border-white/5">
                <h3 className="text-[11px] font-black uppercase tracking-[0.15em] mb-2.5 text-[var(--theme-accent)] m-0">
                  Why this focus matters
                </h3>
                <div className="text-white leading-relaxed text-sm">
                  {activeCard === "learning" ? "Learning proves the organism is internalizing events into reusable knowledge rather than just reacting in the moment." :
                   activeCard === "healing" ? "Healing shows whether the organism can stay useful under stress instead of collapsing when conditions worsen." :
                   activeCard === "autonomy" ? "Autonomy tells you whether the organism can act, not just observe, while staying under human control." :
                   activeCard === "vision" ? "Vision determines whether image-aware workflows can participate in the organism's reasoning loop." :
                   "Operator control makes sure a human remains the final steering layer."}
                </div>
              </div>

              <div className="rounded-[18px] p-[18px] bg-white/[0.028] border border-white/5">
                <h3 className="text-[11px] font-black uppercase tracking-[0.15em] mb-2.5 text-[var(--theme-accent)] m-0">
                  What changed
                </h3>
                <div className="text-white leading-relaxed text-sm">
                  {presenter.bucketComparison ? (
                    <>
                      <strong className="text-[var(--theme-accent)]">{presenter.bucketComparison.label}:</strong> {presenter.bucketComparison.description}
                    </>
                  ) : (
                    <>
                      The organism is currently in <strong className="text-[var(--theme-accent)]">{insights.volumeTrend}</strong> volume posture with <strong className="text-[var(--theme-accent)]">{insights.successTrend}</strong> outcome quality.
                    </>
                  )}
                </div>
              </div>

              <div className="rounded-2xl p-5 bg-[color-mix(in_srgb,var(--theme-accent)_10%,transparent)] border border-[color-mix(in_srgb,var(--theme-accent)_20%,transparent)]">
                <h3 className="text-[11px] font-black uppercase tracking-[0.15em] mb-2.5 text-[var(--theme-accent)] m-0">
                  Try this next
                </h3>
                <div className="text-white leading-relaxed text-[13px] font-bold">
                  {presenter.tryThisNext}
                </div>
              </div>
            </div>
          </article>

          {/* Living Nervous System */}
          <div className="mb-6">
            <LivingNervousSystem
              backendUrl={backendUrl}
              liveData={{
                ollamaReachable: statusQuery.data?.ollama_reachable ?? false,
                installedModels: statusQuery.data?.installed_model_count ?? 0,
                eventCount: statusQuery.data?.event_count ?? 0,
                traceCount: timelinePoints.length,
                healingReady: 100,
                successRate: successRate,
                cacheSize: cacheSize,
                visionAvailable: visionRuntimeReady,
                routerStatus: routerStats.status,
                routerRouted: routerStats.total_routed,
                routerModel: routerStats.active_model,
                criticAcceptRate: criticAcceptRate,
                criticStatus: criticStats.status,
              }}
            />
          </div>

          <OrganismNarrator
            backendUrl={backendUrl}
            ollamaReachable={statusQuery.data?.ollama_reachable ?? false}
            successRate={successRate}
            eventCount={statusQuery.data?.event_count ?? 0}
            healingReady={100}
            traceCount={timelinePoints.length}
          />

          <div className="grid grid-cols-[repeat(auto-fit,minmax(200px,1fr))] gap-3 mb-6">
            <SubsystemInteractiveCard id="ollama" label="Ollama" color="#22c55e" health={statusQuery.data?.ollama_reachable ? 100 : 0} activity={statusQuery.data?.ollama_reachable ? 85 : 0} sublabel={`${statusQuery.data?.installed_model_count ?? 0} models`} backendUrl={backendUrl} prompt="You are Ollama. Report your current status in 3 bullet points. Be direct and technical." />
            <SubsystemInteractiveCard id="router" label="Router" color="#7dd3fc" health={routerStats.success_rate} activity={routerStats.status === "active" ? 90 : 30} sublabel={routerStats.status === "active" ? `${routerStats.total_routed} routed · ${routerStats.active_model}` : "idle — awaiting traces"} backendUrl={backendUrl} prompt={`You are the model router. Status: ${routerStats.status}. You have routed ${routerStats.total_routed} traces with ${routerStats.success_rate}% success. Active model: ${routerStats.active_model}. Explain what you are doing right now in 2 sentences.`} />
            <SubsystemInteractiveCard id="critic" label="Critic" color="#f472b6" health={criticAcceptRate} activity={criticStats.total_evaluated > 0 ? criticAcceptRate : 20} sublabel={`${criticAcceptRate}% accept · ${criticStats.verdict}`} backendUrl={backendUrl} prompt={`You are the AI critic evaluator. Status: ${criticStats.status}. Acceptance rate: ${criticAcceptRate}%. You have evaluated ${criticStats.total_evaluated} outputs (${criticStats.accepted} accepted, ${criticStats.rejected} rejected). Verdict: ${criticStats.verdict}. Give a 2-sentence quality assessment.`} />
            <SubsystemInteractiveCard id="memory" label="Memory" color="#a78bfa" health={statusQuery.data?.event_count ?? 0 > 0 ? 100 : 50} activity={80} sublabel={`${(statusQuery.data?.event_count ?? 0).toLocaleString()} events`} backendUrl={backendUrl} prompt={`You are the memory subsystem. You have stored ${statusQuery.data?.event_count ?? 0} events. Explain what you store and why it matters in 2 sentences.`} />
            <SubsystemInteractiveCard id="qdrant" label="Qdrant" color="#fb923c" health={100} activity={cacheSize > 0 ? 70 : 30} sublabel={`${cacheSize} cached`} backendUrl={backendUrl} prompt={`You are the Qdrant vector database. You have ${cacheSize} cached vectors. Explain semantic search in 2 sentences.`} />
            <SubsystemInteractiveCard id="healer" label="Healer" color="#34d399" health={100} activity={60} sublabel="100% ready" backendUrl={backendUrl} prompt="You are the self-healing subsystem. All 4 checks (orchestrator, qdrant, ollama, api) are passing. Report your current status and what you are watching for." />
          </div>

          <div className="grid grid-cols-[auto_1fr] gap-3 items-start mb-6">
            <ModelPicker
              models={(statusQuery.data?.installed_models as string[] | undefined) ?? []}
              selected={selectedModel}
              onSelect={setSelectedModel}
            />
            <AgentStepRunner backendUrl={backendUrl} selectedModel={selectedModel} />
          </div>

          <OrganismHero
            activeTheme={presenter.activeTheme}
            activeMessage={presenter.activeMessage}
            isLoading={isLoading}
            eventCount={presenter.activeBucketData ? presenter.activeBucketData.event_count : eventCount}
            totalTimelineEvents={totalTimelineEvents}
            successRate={presenter.activeBucketData ? Math.round((presenter.activeBucketData.success_count / Math.max(1, presenter.activeBucketData.event_count)) * 100) : successRate}
            toolCount={toolCount}
            tickerItems={tickerItems}
            systemReady={systemReady}
            visionRuntimeReady={visionRuntimeReady}
            pulseCards={pulseCards}
            backendUrl={backendUrl}
            formatCompact={formatCompact}
            timelinePoints={timelinePoints}
            timelineLoading={isLoading}
          />
          
          <div className="mb-6">
            <GenomesSection />
          </div>

          <OrganismAnatomySection
            activeCard={activeCard}
            setActiveCard={setActiveCard}
            isLoading={isLoading}
            systemReady={systemReady}
            visionRuntimeReady={visionRuntimeReady}
            visionConfigured={visionConfigured}
            toolCount={toolCount}
            totalTimelineEvents={totalTimelineEvents}
            eventCount={eventCount}
            cacheSize={cacheSize}
            capabilities={capabilities}
            backendUrl={backendUrl}
            eventsPath={statusQuery.data?.events_path}
            ollamaReachable={statusQuery.data?.ollama_reachable}
            environment={statusQuery.data?.environment}
            primaryVisionModel={statusQuery.data?.primary_vision_model}
            statusReady={statusQuery.data?.ready}
            activeTheme={presenter.activeTheme}
            activeMessage={presenter.activeMessage}
            failureRate={failureRate}
            cachedKeys={cachedKeys}
            selectedBucket={selectedBucket}
          />

          <OrganismTimelineSection
            isLoading={isLoading}
            isError={isError}
            errorMessage={errorMessage}
            timelinePoints={timelinePoints}
            totalTimelineSuccess={totalTimelineSuccess}
            totalTimelinePartial={totalTimelinePartial}
            totalTimelineFail={totalTimelineFail}
            latestBucket={latestBucket}
            activeAccent={presenter.activeTheme.accent}
            width={width}
            height={height}
            padding={padding}
            allEventsArea={allEventsArea}
            allEventsLine={allEventsLine}
            successLine={successLine}
            partialLine={partialLine}
            failLine={failLine}
            selectedBucket={selectedBucket}
            onSelectBucket={setSelectedBucket}
          />

          {interactionMode !== "observe" && (
            <OrganismTutorSection
              activeCard={activeCard}
              activeTheme={presenter.activeTheme}
              tutorMeta={presenter.tutorMeta}
              tutorialContent={presenter.tutorialContent}
              showDeepTutorial={showDeepTutorial || interactionMode === "drill"}
              setShowDeepTutorial={setShowDeepTutorial}
              interactionMode={interactionMode}
            />
          )}
        </div>

        <div className="grid gap-4 mt-6 pb-12">
          <DebateRoomPanel backendUrl={backendUrl} />
          <SelfHealingPosturePanel backendUrl={backendUrl} />
          <HealingTrigger backendUrl={backendUrl} />
          <GenerationHistory backendUrl={backendUrl} />
          <div className="grid grid-cols-[repeat(auto-fit,minmax(360px,1fr))] gap-4">
            <MemorySearchPanel />
            <ReplayDashboard />
            <OmniDevInterface organismId="main" backendUrl={backendUrl} />
          </div>
        </div>

        <div className="mt-6 p-4 rounded-2xl bg-black/35 border border-white/10">
          <h2 className="text-sky-300 font-extrabold mb-2 text-base m-0">
            V10 Live Swarm Feed
          </h2>

          <div className="max-h-[260px] overflow-auto text-xs focus-visible:ring-2 focus-visible:ring-sky-300 focus:outline-none" tabIndex={0} aria-live="polite" role="log">
            {swarmV10Feed.length === 0 ? (
              <div className="text-white/40">
                Waiting for swarm events...
              </div>
            ) : (
              swarmV10Feed.map((e, i) => (
                <div key={i} className="mb-2 text-white/75">
                  <span className="text-purple-400 font-bold">
                    {e.status ?? "unknown"}
                  </span>
                  {" | score: "}
                  {typeof e.score === "number" ? e.score.toFixed(2) : "—"}
                  {" | branch: "}
                  {e.branch ?? "—"}
                </div>
              ))
            )}
          </div>
        </div>
      </main>
    </>
  );
}
