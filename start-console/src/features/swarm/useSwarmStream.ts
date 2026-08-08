import { useEffect, useState, useRef } from "react";

export interface CIResult {
  type?: string;
  event?: string;
  payload?: {
    score?: number;
    branch?: string;
  };
  status?: string;
  score?: number;
  branch?: string | null;
}

export function useSwarmStream(backendUrl: string) {
  const [swarmV10Feed, setSwarmV10Feed] = useState<CIResult[]>([]);
  const [swarmCockpit, setSwarmCockpit] = useState({
    ciPass: 0,
    ciFail: 0,
    avgScore: 0,
    lastBranch: null as string | null
  });

  const bufferRef = useRef<CIResult[]>([]);

  useEffect(() => {
    if (!backendUrl) return;

    // Declared before es.onerror so the error handler can clear it: an error
    // closes the connection for good here, so leaving the flush timer running
    // would keep a dead 500ms interval alive until unmount.
    let flushInterval: ReturnType<typeof setInterval> | undefined;

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
      console.error("EventSource stream error:", err);
      // Close the connection on error to prevent infinite reconnection loops on 401s or 500s
      es.close();
      if (flushInterval !== undefined) {
        clearInterval(flushInterval);
        flushInterval = undefined;
      }
    };

    flushInterval = setInterval(() => {
      if (bufferRef.current.length > 0) {
        const batch = [...bufferRef.current];
        bufferRef.current = [];

        let newPass = 0;
        let newFail = 0;
        let newScoreSum = 0;
        let lastBranch: string | null = null;

        for (const data of batch) {
          const score = data.payload?.score ?? data.score ?? 0;
          const eventType = data.event ?? data.type;
          if (eventType === "CI_RESULT") {
            if (score >= 0.85) newPass++;
            else newFail++;
          }
          newScoreSum += score;
          lastBranch = data.payload?.branch ?? data.branch ?? lastBranch;
        }

        setSwarmCockpit((prev) => ({
          ciPass: prev.ciPass + newPass,
          ciFail: prev.ciFail + newFail,
          avgScore: (prev.avgScore + (newScoreSum / batch.length)) / 2,
          lastBranch: lastBranch ?? prev.lastBranch
        }));

        setSwarmV10Feed((prev) => [...batch.reverse(), ...prev].slice(0, 50));
      }
    }, 500);

    return () => {
      es.close();
      if (flushInterval !== undefined) {
        clearInterval(flushInterval);
      }
    };
  }, [backendUrl]);

  return { swarmV10Feed, swarmCockpit };
}
