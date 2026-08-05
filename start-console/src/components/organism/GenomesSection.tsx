import { useEffect, useState } from "react"
import { RefreshCw, Brain, HeartPulse } from "lucide-react"
import { api } from "../../lib/api"
import { appConfig } from "../../lib/config"

import { OrganismConstellation } from "./OrganismConstellation"
import "../../styles/genomes.css"


export function GenomesSection() {
  const [generationData, setGenerationData] = useState<any>(null)
  const [loading, setLoading] = useState(true)

  const loadData = async () => {
    try {
      const result = await api.getAdminGeneration(appConfig.backendBaseUrl)
      setGenerationData(result)
    } catch (e) {
      console.error("Failed to load generation", e)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadData()
    const interval = setInterval(loadData, 5000)
    return () => clearInterval(interval)
  }, [])

  const population = generationData?.population || []

  return (
    <div style={{
      marginTop: 24,
      padding: 16,
      borderRadius: 16,
      background: "var(--bg-alt, rgba(0,0,0,0.35))",
      border: "1px solid var(--border, rgba(255,255,255,0.1))"
    }}>
      <div className="genomes-header" style={{ marginBottom: "1.5rem" }}>
        <div>
          <h2 className="genomes-title" style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '1.5rem', margin: 0 }}>
            <HeartPulse size={24} className="heartbeat-icon" /> Live Swarm Genomes
          </h2>
          <div style={{ color: 'var(--fg-muted)', marginTop: '0.25rem', fontSize: '0.85rem' }}>
            {generationData
              ? `Generation ${generationData.generation || 0} • ${population.length} organisms active`
              : 'Loading swarm population data...'}
          </div>
        </div>
        
        <button 
          onClick={loadData}
          className="btn-primary"
          style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '0.5rem 1rem', borderRadius: '999px', background: 'rgba(255,255,255,0.1)', border: 'none', color: 'white', cursor: 'pointer' }}
        >
          <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
        </button>
      </div>


      {population.length > 0 ? (
        <OrganismConstellation population={population} />
      ) : (
        <div className="genome-card" style={{ textAlign: 'center', padding: '2rem' }}>
          <Brain size={32} style={{ opacity: 0.2, margin: '0 auto 0.5rem auto' }} />
          <h4 style={{ margin: 0, fontWeight: 500 }}>No Population Data</h4>
          <p style={{ color: 'var(--fg-muted)', fontSize: '0.85rem', marginTop: '0.5rem' }}>
            Run a simulation with <code>rob /simulation play</code> to evolve genomes.
          </p>
        </div>
      )}
    </div>
  )
}
