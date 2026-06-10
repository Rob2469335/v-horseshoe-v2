export default function MemorySearchPage() {
  const placeholders = [
    { label: "Vector store", value: "Qdrant / Chroma", detail: "Local semantic storage for high-speed retrieval." },
    { label: "Embedding model", value: "all-minilm-l6-v2", detail: "Active model for converting text into math vectors." },
    { label: "Search traces", value: "4,208 entries", detail: "Total number of searchable historical events." }
  ]

  return (
    <section className="page">
      <h1>Memory/Search</h1>
      <p>Phase 3: Deep inspection of vector memory, semantic retrieval patterns, and historical search traces.</p>

      <div className="agent-layout" style={{ marginTop: "24px" }}>
        <article className="agent-panel" style={{ borderLeft: "4px solid var(--page-accent)" }}>
          <h2>Memory Posture</h2>
          <div style={{ display: "grid", gap: "20px", marginTop: "16px" }}>
            {placeholders.map(item => (
              <div key={item.label} style={{ background: "rgba(0,0,0,0.2)", padding: "16px", borderRadius: "12px", border: "1px solid var(--border)" }}>
                <div style={{ color: "var(--page-accent)", fontWeight: "800", fontSize: "12px", textTransform: "uppercase", marginBottom: "4px" }}>{item.label}</div>
                <div style={{ fontSize: "20px", fontWeight: "900", marginBottom: "8px" }}>{item.value}</div>
                <div style={{ color: "var(--text-muted)", fontSize: "14px" }}>{item.detail}</div>
              </div>
            ))}
          </div>
        </article>

        <article className="agent-panel">
          <h2>Retrieval Stream</h2>
          <div style={{ color: "var(--text-muted)", textAlign: "center", padding: "60px 20px" }}>
            <div style={{ fontSize: "40px", marginBottom: "20px" }}>🧠</div>
            <div style={{ fontWeight: "700", color: "var(--text-soft)" }}>Semantic engine offline</div>
            <p style={{ fontSize: "13px", marginTop: "8px" }}>Connect a vector database to begin monitoring retrieval traces in real-time.</p>
          </div>
        </article>
      </div>
    </section>
  )
}
