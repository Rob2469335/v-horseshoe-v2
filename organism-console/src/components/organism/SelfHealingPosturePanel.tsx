import { useEffect, useState } from "react"

interface Props {
  backendUrl: string
}

interface ApprovalRequest {
  id: string
  component: string
  action: string
  reason: string
  status: string
  created_at: string
}

interface AuditRecord {
  timestamp: string
  component: string
  action: string
  executed: boolean
  repair?: { status: string; detail: string }
  verification?: { verified: boolean; detail: string }
}

export function SelfHealingPosturePanel({ backendUrl }: Props) {
  const [loading, setLoading] = useState(false)
  const [data, setData] = useState<any>(null)
  const [error, setError] = useState<string | null>(null)
  const [actionLoading, setActionLoading] = useState<string | null>(null)

  const fetchData = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`${backendUrl}/features/healing-overview`)
      if (!res.ok) {
        throw new Error(`Failed to fetch overview: ${res.statusText}`)
      }
      const json = await res.json()
      setData(json)
    } catch (err: any) {
      setError(err.message || "Could not retrieve self-healing status.")
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchData()
    // Poll every 15 seconds
    const interval = setInterval(fetchData, 15000)
    return () => clearInterval(interval)
  }, [backendUrl])

  const handleApproval = async (id: string, approve: boolean) => {
    setActionLoading(id)
    try {
      const decision = approve ? "approve" : "reject"
      const res = await fetch(`${backendUrl}/features/healing-approvals/${id}/${decision}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" }
      })
      if (!res.ok) {
        throw new Error(`Approval decision failed: ${res.statusText}`)
      }

      if (approve) {
        // Execute the approved action immediately
        const execRes = await fetch(`${backendUrl}/features/healing-approvals/${id}/execute`, {
          method: "POST"
        })
        if (!execRes.ok) {
          throw new Error(`Execution of approved action failed: ${execRes.statusText}`)
        }
      }

      // Refresh data
      await fetchData()
    } catch (err: any) {
      alert(`Error processing approval action: ${err.message}`)
    } finally {
      setActionLoading(null)
    }
  };

  const summary = data?.summary || { recent_failures: 0, active_incidents: 0 }
  const readiness = data?.readiness ?? 100
  const actions: AuditRecord[] = data?.actions || []
  const pendingRequests: ApprovalRequest[] = (data?.approvals?.requests || []).filter(
    (r: any) => r.status === "pending"
  )

  const getReadinessColor = (val: number) => {
    if (val >= 90) return "#22c55e"
    if (val >= 70) return "#fbbf24"
    return "#ef4444"
  }

  return (
    <div style={{
      background: "rgba(255, 255, 255, 0.03)",
      border: "1px solid rgba(255, 255, 255, 0.08)",
      borderRadius: 20,
      padding: "20px",
      boxShadow: "0 14px 28px rgba(0,0,0,0.18)",
      display: "grid",
      gap: 16
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <div style={{ fontSize: 11, fontWeight: 700, color: "rgba(255, 255, 255, 0.45)", textTransform: "uppercase", letterSpacing: "0.1em" }}>
            🩹 Self-Healing Posture Panel
          </div>
          <div style={{ fontSize: 14, color: "rgba(255,255,255,0.8)", fontWeight: 600, marginTop: 4 }}>
            Monitor real-time system resilience, readiness logs, and automated recoveries.
          </div>
        </div>
        <button
          onClick={fetchData}
          disabled={loading}
          style={{
            background: "rgba(255,255,255,0.06)",
            border: "1px solid rgba(255,255,255,0.1)",
            borderRadius: 8,
            padding: "6px 12px",
            color: "white",
            fontSize: 12,
            cursor: "pointer"
          }}
        >
          {loading ? "🔄 Syncing..." : "🔄 Refresh"}
        </button>
      </div>

      {error && (
        <div style={{
          padding: "12px 16px",
          borderRadius: 12,
          background: "rgba(239,68,68,0.08)",
          border: "1px solid rgba(239,68,68,0.2)",
          color: "#fca5a5",
          fontSize: 13
        }}>
          ❌ {error}
        </div>
      )}

      {/* Metrics Row */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 12 }}>
        <div style={{ background: "rgba(0,0,0,0.3)", borderRadius: 12, padding: "12px 16px", border: "1px solid rgba(255,255,255,0.04)" }}>
          <div style={{ fontSize: 11, color: "rgba(255,255,255,0.4)", marginBottom: 4, textTransform: "uppercase" }}>Readiness Score</div>
          <div style={{ fontSize: 24, fontWeight: 800, color: getReadinessColor(readiness) }}>{readiness}%</div>
        </div>
        <div style={{ background: "rgba(0,0,0,0.3)", borderRadius: 12, padding: "12px 16px", border: "1px solid rgba(255,255,255,0.04)" }}>
          <div style={{ fontSize: 11, color: "rgba(255,255,255,0.4)", marginBottom: 4, textTransform: "uppercase" }}>Active Anomalies</div>
          <div style={{ fontSize: 24, fontWeight: 800, color: summary.active_incidents > 0 ? "#ef4444" : "#22c55e" }}>
            {summary.active_incidents}
          </div>
        </div>
        <div style={{ background: "rgba(0,0,0,0.3)", borderRadius: 12, padding: "12px 16px", border: "1px solid rgba(255,255,255,0.04)" }}>
          <div style={{ fontSize: 11, color: "rgba(255,255,255,0.4)", marginBottom: 4, textTransform: "uppercase" }}>Recent Failures</div>
          <div style={{ fontSize: 24, fontWeight: 800, color: summary.recent_failures > 0 ? "#f59e0b" : "#22c55e" }}>
            {summary.recent_failures}
          </div>
        </div>
      </div>

      {/* Pending Approvals Section */}
      {pendingRequests.length > 0 && (
        <div style={{ display: "grid", gap: 10 }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: "#f59e0b", textTransform: "uppercase", letterSpacing: "0.05em" }}>
            ⚠️ Pending Recovery Approvals
          </div>
          <div style={{ display: "grid", gap: 8 }}>
            {pendingRequests.map((req) => (
              <div
                key={req.id}
                style={{
                  background: "rgba(245,158,11,0.06)",
                  border: "1px solid rgba(245,158,11,0.2)",
                  borderRadius: 12,
                  padding: "12px 16px",
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center"
                }}
              >
                <div>
                  <div style={{ fontSize: 13, fontWeight: 700, color: "#fef08a" }}>
                    {req.action} ({req.component})
                  </div>
                  <div style={{ fontSize: 12, color: "rgba(255,255,255,0.7)", marginTop: 2 }}>
                    Reason: {req.reason}
                  </div>
                </div>
                <div style={{ display: "flex", gap: 8 }}>
                  <button
                    disabled={actionLoading === req.id}
                    onClick={() => handleApproval(req.id, true)}
                    style={{
                      background: "#22c55e",
                      border: "none",
                      color: "white",
                      fontSize: 11,
                      fontWeight: 700,
                      padding: "6px 12px",
                      borderRadius: 6,
                      cursor: "pointer"
                    }}
                  >
                    {actionLoading === req.id ? "..." : "Approve & Execute"}
                  </button>
                  <button
                    disabled={actionLoading === req.id}
                    onClick={() => handleApproval(req.id, false)}
                    style={{
                      background: "#ef4444",
                      border: "none",
                      color: "white",
                      fontSize: 11,
                      fontWeight: 700,
                      padding: "6px 12px",
                      borderRadius: 6,
                      cursor: "pointer"
                    }}
                  >
                    Reject
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Audit Log Section */}
      <div style={{ display: "grid", gap: 8 }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: "rgba(255,255,255,0.6)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
          📜 Recovery Actions Audit Log
        </div>

        <div style={{
          background: "rgba(0,0,0,0.2)",
          border: "1px solid rgba(255,255,255,0.05)",
          borderRadius: 12,
          maxHeight: "220px",
          overflowY: "auto"
        }}>
          {actions.length === 0 ? (
            <div style={{ padding: "16px", color: "rgba(255,255,255,0.4)", fontSize: 13, textAlign: "center" }}>
              No recent recovery actions recorded.
            </div>
          ) : (
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
              <thead>
                <tr style={{ borderBottom: "1px solid rgba(255,255,255,0.08)", background: "rgba(255,255,255,0.02)" }}>
                  <th style={{ padding: "8px 12px", textAlign: "left", color: "rgba(255,255,255,0.4)" }}>Time</th>
                  <th style={{ padding: "8px 12px", textAlign: "left", color: "rgba(255,255,255,0.4)" }}>Component</th>
                  <th style={{ padding: "8px 12px", textAlign: "left", color: "rgba(255,255,255,0.4)" }}>Action</th>
                  <th style={{ padding: "8px 12px", textAlign: "center", color: "rgba(255,255,255,0.4)" }}>Status</th>
                  <th style={{ padding: "8px 12px", textAlign: "center", color: "rgba(255,255,255,0.4)" }}>Verified</th>
                </tr>
              </thead>
              <tbody>
                {actions.map((act, index) => {
                  const verified = act.verification?.verified ?? true
                  const timeStr = act.timestamp ? new Date(act.timestamp).toLocaleTimeString() : "—"
                  return (
                    <tr key={index} style={{ borderBottom: "1px solid rgba(255,255,255,0.04)" }}>
                      <td style={{ padding: "8px 12px", color: "rgba(255,255,255,0.5)" }}>{timeStr}</td>
                      <td style={{ padding: "8px 12px", fontWeight: 600 }}>{act.component}</td>
                      <td style={{ padding: "8px 12px" }}>{act.action}</td>
                      <td style={{ padding: "8px 12px", textAlign: "center" }}>
                        <span style={{
                          background: act.executed ? "rgba(34,197,94,0.1)" : "rgba(239,68,68,0.1)",
                          color: act.executed ? "#86efac" : "#fca5a5",
                          padding: "2px 6px",
                          borderRadius: 4,
                          fontSize: 10
                        }}>
                          {act.executed ? "SUCCESS" : "FAILED"}
                        </span>
                      </td>
                      <td style={{ padding: "8px 12px", textAlign: "center" }}>
                        <span style={{
                          color: verified ? "#86efac" : "#fca5a5",
                          fontSize: 14
                        }}>
                          {verified ? "✓" : "✗"}
                        </span>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  )
}
