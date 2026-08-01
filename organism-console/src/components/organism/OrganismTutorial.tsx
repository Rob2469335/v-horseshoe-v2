import React from "react";

interface OrganismTutorialProps {
  modeSummary: { title: string; detail: string };
  activeMessage: string;
  interactionMode: "observe" | "teach" | "drill";
  setInteractionMode: (mode: "observe" | "teach" | "drill") => void;
  selectedBucket: string | null;
  activeTheme: { accent: string; glow: string };
  backendUrl: string;
  successRate: number;
  failureRate: number;
  latestBucket: string | null;
}

export function OrganismTutorial({
  modeSummary,
  activeMessage,
  interactionMode,
  setInteractionMode,
  selectedBucket,
  activeTheme,
  backendUrl,
  successRate,
  failureRate,
  latestBucket
}: OrganismTutorialProps) {
  return (
    <article className="relative overflow-hidden rounded-[30px] p-6 bg-gradient-to-b from-white/5 to-white/5 border border-white/10 shadow-[0_18px_56px_rgba(0,0,0,0.24)] grid gap-5">
      <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/5 to-transparent -translate-x-[120%] animate-[scanSweep_6.4s_linear_infinite]" />

      <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1.25fr)_minmax(320px,0.75fr)] gap-5 items-center">
        <div>
          <div className="text-xs font-black uppercase tracking-[0.16em] mb-2.5 text-[var(--theme-accent)]">
            Organism tutor / living systems console
          </div>
          <h1 className="m-0 text-white text-[clamp(2rem,4vw,3.6rem)] leading-[1.02] font-black tracking-[-0.04em]">
            Adaptive organism control
          </h1>
          <p className="mt-3.5 text-blue-50/75 leading-relaxed max-w-[780px]">
            This page teaches what the organism is doing, how healthy it is, and where learning, self-heal, autonomy, vision, and operator control are changing in real time.
          </p>

          <div className="flex flex-wrap gap-3 mt-[18px]">
            {[
              { label: "Backend", value: backendUrl, accent: activeTheme.accent },
              { label: "Success", value: `${successRate}%`, accent: "#22c55e" },
              { label: "Failure", value: `${failureRate}%`, accent: "#f97316" },
              { label: "Latest bucket", value: latestBucket, accent: "#7dd3fc" }
            ].map((chip) => (
              <div
                key={chip.label}
                className="flex items-center gap-2.5 px-3.5 py-2.5 rounded-full bg-white/5 border border-white/10"
              >
                <span
                  className="w-2.5 h-2.5 rounded-full animate-[statusBlink_2.4s_ease-in-out_infinite] bg-[var(--chip-color)] shadow-[0_0_18px_var(--chip-color)]"
                  style={{ "--chip-color": chip.accent } as React.CSSProperties}
                />
                <span className="text-white/60 text-xs font-extrabold uppercase tracking-[0.12em]">
                  {chip.label}
                </span>
                <span className="text-white font-extrabold">
                  {chip.value}
                </span>
              </div>
            ))}
          </div>
        </div>

        <aside className="rounded-3xl p-5 bg-[#040a18]/55 backdrop-blur-md border border-[color-mix(in_srgb,var(--theme-accent)_20%,transparent)] shadow-[0_18px_42px_var(--theme-glow)]">
          <h2 className="text-[11px] font-black uppercase tracking-[0.16em] mb-2.5 text-[var(--theme-accent)] m-0">
            Tutor interpretation
          </h2>
          <h3 className="text-white text-[25px] font-black mb-2.5 m-0">
            {modeSummary.title}
          </h3>
          <div className="text-blue-50/75 leading-relaxed text-sm">
            {modeSummary.detail}
          </div>
          <div className="mt-4 p-3.5 rounded-2xl text-blue-50/85 leading-relaxed text-[13px] bg-[color-mix(in_srgb,var(--theme-accent)_6%,transparent)] border border-[color-mix(in_srgb,var(--theme-accent)_15%,transparent)]">
            {activeMessage}
          </div>
        </aside>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-4">
        <nav aria-label="Interaction Modes" className="flex flex-wrap gap-3 bg-black/40 p-1.5 rounded-full border border-white/10">
          {[
            { key: "observe", label: "Observe", icon: "👁️" },
            { key: "teach", label: "Teach", icon: "🎓" },
            { key: "drill", label: "Drill", icon: "🔬" }
          ].map((mode) => {
            const isActive = interactionMode === mode.key;
            return (
              <button
                key={mode.key}
                type="button"
                onClick={() => setInteractionMode(mode.key as "observe" | "teach" | "drill")}
                aria-pressed={isActive}
                className={`flex items-center gap-2 px-4 py-2.5 rounded-full border-none font-extrabold text-[13px] cursor-pointer transition-all duration-200 ease-out focus-visible:ring-2 focus-visible:ring-white focus:outline-none ${
                  isActive ? "text-white -translate-y-[1px] bg-[var(--theme-accent)] shadow-[0_8px_18px_var(--theme-glow)]" : "text-white/80 translate-y-0 bg-black/40 shadow-none"
                }`}
              >
                <span>{mode.icon}</span>
                {mode.label}
              </button>
            );
          })}
        </nav>

        <div className="px-3.5 py-2.5 rounded-2xl bg-white/5 border border-white/10 text-blue-50/75 text-[13px] leading-relaxed">
          {selectedBucket
            ? `Focused on bucket ${selectedBucket}. Click the timeline background to return to live flow.`
            : "No bucket pinned. The organism is in live monitoring mode."}
        </div>
      </div>
    </article>
  );
}
