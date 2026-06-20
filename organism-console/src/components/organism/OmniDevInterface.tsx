import React, { useState } from "react";
import { organismTheme } from "../../features/organism/organism-theme";

interface OmniDevInterfaceProps {
  backendUrl: string;
  organismId: string;
}

export const OmniDevInterface: React.FC<OmniDevInterfaceProps> = ({ organismId, backendUrl }) => {
  const [task, setTask] = useState("");
  const [result, setResult] = useState("");
  const [loading, setLoading] = useState(false);

  const app = { apiUrl: backendUrl };

  const handleRun = async () => {
    if (!task) return;
    setLoading(true);
    setResult("");

    try {
      const response = await fetch(`${app.apiUrl}/omnidev/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task, organismId })
      });

      const data = await response.json();
      setResult(data.result || data.error);
    } catch (error) {
      setResult(`Error: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setLoading(false);
    }
  };

  const theme = organismTheme;

  return (
    <div style={{
      backgroundColor: theme.surface.panel,
      borderRadius: "8px",
      padding: "16px",
      marginTop: "16px",
      border: `1px solid ${theme.surface.border}`
    }}>
      <h3 style={{ color: theme.surface.text, marginBottom: "12px" }}>
        🤖 OmniDev Assistant
      </h3>
      <textarea
        value={task}
        onChange={(e) => setTask(e.target.value)}
        placeholder="Describe what you want OmniDev to do..."
        style={{
          width: "100%",
          minHeight: "80px",
          backgroundColor: theme.surface.page,
          color: theme.surface.text,
          border: `1px solid ${theme.surface.border}`,
          borderRadius: "4px",
          padding: "8px",
          fontFamily: "monospace",
          marginBottom: "8px"
        }}
      />
      <button
        onClick={handleRun}
        disabled={loading}
        style={{
          backgroundColor: loading ? "rgba(255,255,255,0.08)" : theme.subsystem.learning.accent,
          color: "#000",
          padding: "8px 16px",
          borderRadius: "4px",
          border: "none",
          cursor: loading ? "not-allowed" : "pointer",
          fontWeight: "bold"
        }}
      >
        {loading ? "Running..." : "Run Task"}
      </button>
      {result && (
        <div style={{
          marginTop: "12px",
          backgroundColor: theme.surface.page,
          padding: "8px",
          borderRadius: "4px",
          color: theme.surface.text,
          fontFamily: "monospace",
          fontSize: "12px"
        }}>
          {result}
        </div>
      )}
    </div>
  );
};

