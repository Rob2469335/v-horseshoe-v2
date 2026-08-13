import { useEffect, useState, useRef } from "react";

export interface CIResult {
  event?: string;
  type?: string;
  payload?: {
    score?: number;
    branch?: string;
    model?: string;
    duration_ms?: number;
    summary?: string;
  };
  status?: string;
  score?: number;
  branch?: string | null;
  model?: string;
  id?: string;
}

export function useSwarmStream(backendUrl: string) {
  const [swarmV10Feed, setSwarmV10Feed] = useState<CIResult[]>([]);
  const [swarmCockpit, setSwarmCockpit] = useState({
    ciPass: 0,
    ciFail: 0,
    avgScore: 0,
    lastBranch: null as string | null,
    _scoreSamples: 0,
    _scoreSum: 0
  });

  const bufferRef = useRef<CIResult[]>([]);

  useEffect(() => {
    if (!backendUrl) return;

    const es = new EventSource(`${backendUrl}/swarm/v10/stream`);

    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as CIResult;
        bufferRef.current.push(data);
      } catch (e) {
        console.error("Failed to parse stream event", e);
      }
    };

    es.onerror = (err) => {
      console.error("SSE stream error", err);
      es.close();
    };

    const flushInterval = setInterval(() => {
      if (bufferRef.current.length > 0) {
        const batch = [...bufferRef.current];
        bufferRef.current = [];

        let newPass = 0;
        let newFail = 0;
        let newScoreSum = 0;
        let newScoreCount = 0;
        let lastBranch: string | null = null;

        for (const data of batch) {
          const score = data.payload?.score ?? data.score;
          const eventType = data.event ?? data.type;

          // The v10 stream relays EventBus events, whose only real event types
          // are GENERATION_COMPLETED (and the ping heartbeat). Count those as
          // passes; no failure event type exists on the bus, so fail stays at
          // its honest 0.
          if (eventType === "GENERATION_COMPLETED") newPass++;

          if (typeof score === "number") { newScoreSum += score; newScoreCount++; }
          lastBranch = data.payload?.branch ?? data.branch ?? lastBranch;
        }

        // Keep a running TRUE average (sum of all scores / count seen), not an
        // average-of-averages that drifts between batches.
        setSwarmCockpit((prev) => {
          const samples = (prev._scoreSamples ?? 0) + newScoreCount;
          const scoreSum = (prev._scoreSum ?? 0) + newScoreSum;
          const avg = samples > 0 ? scoreSum / samples : prev.avgScore;
          return {
            ciPass: prev.ciPass + newPass,
            ciFail: prev.ciFail + newFail,
            avgScore: avg,
            lastBranch: lastBranch ?? prev.lastBranch,
            _scoreSamples: samples,
            _scoreSum: scoreSum,
          };
        });

        setSwarmV10Feed((prev) => [...batch.reverse(), ...prev].slice(0, 50));
      }
    }, 500);

    return () => {
      es.close();
      clearInterval(flushInterval);
    };
  }, [backendUrl]);

  return { swarmV10Feed, swarmCockpit };
}
