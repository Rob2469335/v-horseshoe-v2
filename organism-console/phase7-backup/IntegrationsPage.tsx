export default function IntegrationsPage() {
  const providers = [
    { name: "OpenAI", status: "online", type: "LLM" },
    { name: "Anthropic", status: "online", type: "LLM" },
    { name: "Ollama", status: "online", type: "Local" },
    { name: "GitHub", status: "offline", type: "Tools" },
    { name: "Slack", status: "offline", type: "Communication" }
  ]

  return (
    <section className="page">
      <h1>Integrations</h1>
      <p>Phase 4: Manage provider connections, API keys, and external system adapters.</p>

      <div className="ops-grid" style={{ marginTop: "24px" }}>
        {providers.map(p => (
          <article key={p.name} className="ops-card" style={{ 
            borderLeft: `4px solid ${p.status === 'online' ? 'var(--success)' : 'var(--border)'}`,
            background: p.status === 'online' ? 'var(--page-accent-tint)' : 'rgba(20,27,41,0.4)'
          }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "start" }}>
              <div style={{ fontSize: "18px", fontWeight: "900" }}>{p.name}</div>
              <span className={`status-badge status-badge--${p.status === 'online' ? 'success' : 'neutral'}`}>
                {p.status}
              </span>
            </div>
            <div style={{ color: "var(--text-muted)", fontSize: "12px", fontWeight: "700", textTransform: "uppercase", marginTop: "8px" }}>
              Type: {p.type}
            </div>
          </article>
        ))}
      </div>

      <article className="agent-panel" style={{ marginTop: "24px", background: "linear-gradient(135deg, var(--page-accent-tint), transparent)" }}>
        <h2>Connected System Surface</h2>
        <div style={{ padding: "40px", textAlign: "center", border: "2px dashed var(--border)", borderRadius: "20px", marginTop: "16px" }}>
          <p style={{ color: "var(--text-soft)", fontWeight: "700" }}>No external adapters currently broadcasting.</p>
          <button style={{ 
            marginTop: "20px",
            padding: "10px 24px",
            background: "var(--page-accent)",
            color: "#000",
            border: "none",
            borderRadius: "99px",
            fontWeight: "900",
            cursor: "pointer",
            boxShadow: "0 0 20px var(--page-accent-glow)"
          }}>
            Add New Integration
          </button>
        </div>
      </article>
    </section>
  )
}
