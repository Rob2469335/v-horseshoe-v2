import React from "react";

interface CockpitStats {
  ciPass: number;
  ciFail: number;
  avgScore: number;
  lastBranch: string | null;
}

export function OrganismCockpit({ stats }: { stats: CockpitStats }) {
  return (
    <div className="sticky top-0 z-50 mb-4 p-3 rounded-2xl bg-black/55 border border-white/10 backdrop-blur-md">
      <h2 className="text-sky-300 font-black mb-1.5 text-base m-0">
        🧠 Swarm Cockpit Live Control
      </h2>

      <div className="flex gap-3 text-xs" aria-live="polite" role="status">
        <span className="text-green-500">✔ Pass: {stats.ciPass}</span>
        <span className="text-orange-500">✖ Fail: {stats.ciFail}</span>
        <span className="text-purple-400">Score: {stats.avgScore?.toFixed?.(2)}</span>
        <span className="text-sky-300">Branch: {stats.lastBranch}</span>
      </div>
    </div>
  );
}
