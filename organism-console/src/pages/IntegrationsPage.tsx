export default function IntegrationsPage() {
  const providers = [
    { name: "OpenAI", status: "online", type: "LLM" },
    { name: "Anthropic", status: "online", type: "LLM" },
    { name: "Ollama", status: "online", type: "Local" },
    { name: "GitHub", status: "offline", type: "Tools" },
    { name: "Slack", status: "offline", type: "Communication" }
  ] as const

  return (
    <section className="page">
      <header className="page__header">
        <div className="page__eyebrow">
          <span className="page__eyebrow-dot" />
          Integration surface
        </div>
        <h1>Integrations</h1>
        <p>Manage provider connections, API keys, and external system adapters.</p>
      </header>

      <div
        className="ops-grid"
        style={{
          marginTop: "20px",
          gap: "16px"
        }}
      >
        {providers.map((p) => (
          <article
            key={p.name}
            className="ops-card panel-surface"
            style={{
              position: "relative",
              overflow: "hidden",
              background: p.status === "online" ? "var(--page-accent-tint)" : "rgba(20,27,41,0.4)",
              border: "1px solid rgba(255,255,255,0.08)",
              boxShadow: p.status === "online"
                ? "0 16px 40px rgba(0,0,0,0.20)"
                : "0 12px 32px rgba(0,0,0,0.16)"
            }}
          >
            <div
              style={{
                position: "absolute",
                inset: "0 auto auto 0",
                width: "100%",
                height: "1px",
                background: p.status === "online"
                  ? "linear-gradient(90deg, var(--page-accent), transparent 72%)"
                  : "linear-gradient(90deg, rgba(255,255,255,0.12), transparent 72%)",
                opacity: 0.8,
                pointerEvents: "none"
              }}
            />

            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "start", gap: "12px" }}>
              <div style={{ display: "grid", gap: "10px" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                  <span
                    style={{
                      width: "10px",
                      height: "10px",
                      borderRadius: "50%",
                      background: p.status === "online" ? "var(--success)" : "var(--text-muted)",
                      boxShadow: p.status === "online" ? "0 0 14px rgba(16,185,129,0.45)" : "none",
                      flexShrink: 0
                    }}
                  />
                  <div style={{ fontSize: "18px", fontWeight: "900", lineHeight: 1.1 }}>{p.name}</div>
                </div>

                <div
                  style={{
                    display: "inline-flex",
                    width: "fit-content",
                    alignItems: "center",
                    gap: "8px",
                    padding: "6px 10px",
                    borderRadius: "999px",
                    background: "rgba(255,255,255,0.04)",
                    border: "1px solid rgba(255,255,255,0.08)",
                    color: "var(--text-muted)",
                    fontSize: "11px",
                    fontWeight: "800",
                    textTransform: "uppercase",
                    letterSpacing: "0.08em"
                  }}
                >
                  {p.type} provider
                </div>
              </div>

              <span className={`status-badge status-badge--${p.status === "online" ? "success" : "neutral"}`}>
                {p.status}
              </span>
            </div>

            <div
              style={{
                marginTop: "16px",
                paddingTop: "14px",
                borderTop: "1px solid rgba(255,255,255,0.07)",
                color: "var(--text-soft)",
                fontSize: "13px",
                lineHeight: 1.65
              }}
            >
              {p.status === "online"
                ? `Connection to ${p.name} is visible to the console and ready for operator review.`
                : `${p.name} is configured as an available surface but is not currently broadcasting.`}
            </div>
          </article>
        ))}
      </div>

      <article
        className="agent-panel panel-surface"
        style={{
          marginTop: "20px",
          overflow: "hidden",
          position: "relative"
        }}
      >
        <div
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: "10px",
            padding: "6px 12px",
            borderRadius: "999px",
            background: "var(--page-accent-tint)",
            color: "var(--page-accent)",
            border: "1px solid rgba(255,255,255,0.08)",
            fontSize: "11px",
            fontWeight: "800",
            letterSpacing: "0.1em",
            textTransform: "uppercase",
            marginBottom: "14px"
          }}
        >
          <span
            style={{
              width: "8px",
              height: "8px",
              borderRadius: "50%",
              background: "var(--page-accent)",
              boxShadow: "0 0 14px var(--page-accent-glow)"
            }}
          />
          Adapter staging area
        </div>

        <h2>Connected system surface</h2>

        <div
          style={{
            padding: "30px",
            textAlign: "center",
            border: "1px dashed var(--border-rich)",
            borderRadius: "18px",
            marginTop: "16px",
            background: "linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0.015))",
            boxShadow: "inset 0 1px 0 rgba(255,255,255,0.03)"
          }}
        >
          <div
            style={{
              width: "52px",
              height: "52px",
              borderRadius: "16px",
              margin: "0 auto 16px",
              display: "grid",
              placeItems: "center",
              background: "rgba(255,255,255,0.05)",
              border: "1px solid rgba(255,255,255,0.08)",
              color: "var(--page-accent)",
              fontSize: "22px",
              boxShadow: "0 10px 30px rgba(0,0,0,0.16)"
            }}
          >
            +
          </div>

          <p
            style={{
              color: "var(--text-soft)",
              fontWeight: "700",
              margin: 0,
              lineHeight: 1.7
            }}
          >
            No external adapters are currently broadcasting.
          </p>

          <p
            style={{
              color: "var(--text-muted)",
              margin: "10px auto 0",
              maxWidth: "520px",
              fontSize: "14px",
              lineHeight: 1.7
            }}
          >
            Bring a new provider online to expand the organism's action surface and external system reach.
          </p>

          <button
            style={{
              marginTop: "18px",
              padding: "11px 22px",
              minHeight: "44px",
              background: "linear-gradient(180deg, rgba(255,255,255,0.10), var(--page-accent))",
              color: "#061018",
              border: "1px solid rgba(255,255,255,0.18)",
              borderRadius: "999px",
              fontWeight: "900",
              letterSpacing: "0.06em",
              cursor: "pointer",
              boxShadow: "0 8px 24px var(--page-accent-glow)"
            }}
          >
            Add new integration
          </button>
        </div>
      </article>
    </section>
  )
}

