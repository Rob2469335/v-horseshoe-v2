import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import ForceGraph2D from 'react-force-graph-2d';

export function OrganismConstellation({ population = [] }: { population?: any[] }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const fgRef = useRef<any>(null);
  const [hoverNode, setHoverNode] = useState<any>(null);
  const [dimensions, setDimensions] = useState({ width: 800, height: 400 });

  useEffect(() => {
    if (!containerRef.current) return;
    const observer = new ResizeObserver((entries) => {
      for (let entry of entries) {
        if (entry.contentRect.width > 0) {
          setDimensions({
            width: entry.contentRect.width,
            height: 500
          });
        }
      }
    });
    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, []);

  // Map backend population data into nodes and links
  const graphData = useMemo(() => {
    if (!population || population.length === 0) return { nodes: [], links: [] };

    const nodes = population.map((org, index) => ({
      id: org.id || `agent-${index}`,
      name: org.id || `Agent-${index}`,
      fitness: org.fitness || 0.1,
      genomeType: org.genome?.model || org.model || 'qwen3.5-4b',
      generation: org.genome?.generation || 1
    }));

    // Auto-generate links based on genome similarities
    const links: any[] = [];
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        if (nodes[i].genomeType === nodes[j].genomeType) {
          links.push({
            source: nodes[i].id,
            target: nodes[j].id,
            similarity: 0.8
          });
        }
      }
    }
    
    // Fallback links if graph is completely disconnected
    if (links.length === 0 && nodes.length > 1) {
        for (let i = 0; i < nodes.length - 1; i++) {
            links.push({ source: nodes[i].id, target: nodes[i+1].id, similarity: 0.3 });
        }
    }

    return { nodes, links };
  }, [population]);

  const getGenomeColor = (type: string) => {
    if (type.includes('qwen')) return '#00e5ff'; // Cyan
    if (type.includes('llama')) return '#ff3366'; // Pink
    if (type.includes('gpt')) return '#b366ff'; // Purple
    return '#ffcc00'; // Gold fallback
  };

  const paintNode = useCallback((node: any, ctx: any) => {
    const baseColor = getGenomeColor(node.genomeType);
    
    const time = Date.now() / 400; 
    // Basic hash for offset
    let hash = 0;
    for (let i = 0; i < String(node.id).length; i++) {
        hash = String(node.id).charCodeAt(i) + ((hash << 5) - hash);
    }
    const pulseOffset = Math.abs(hash) % 10;
    const pulse = (Math.sin(time + pulseOffset) + 1) / 2;
    
    // Cap visual fitness so the radius doesn't blow up
    const displayFitness = Math.min(Math.max(node.fitness, 0), 5);
    const baseRadius = 3 + (displayFitness * 1.5);
    const glowRadius = baseRadius + (pulse * (displayFitness + 0.1) * 3) + 2;

    ctx.beginPath();
    ctx.arc(node.x, node.y, glowRadius, 0, 2 * Math.PI, false);
    ctx.fillStyle = baseColor;
    ctx.globalAlpha = 0.05 + (pulse * 0.15);
    ctx.fill();

    ctx.beginPath();
    ctx.arc(node.x, node.y, baseRadius, 0, 2 * Math.PI, false);
    ctx.fillStyle = hoverNode?.id === node.id ? '#ffffff' : baseColor;
    ctx.globalAlpha = 0.9;
    
    ctx.shadowColor = baseColor;
    ctx.shadowBlur = 10;
    ctx.fill();
    
    ctx.shadowBlur = 0;
    ctx.globalAlpha = 1;
  }, [hoverNode]);

  useEffect(() => {
    // We don't reheat the simulation every frame, that breaks the physics engine!
    const interval = setInterval(() => {
        // Just trigger a light redraw for the pulse effect by touching the canvas
        if (fgRef.current) {
            // Setting alpha target to 0 lets it redraw without exploding physics
            fgRef.current.d3AlphaTarget(0);
        }
    }, 100);
    return () => clearInterval(interval);
  }, []);

  return (
    <div ref={containerRef} style={{ position: 'relative', width: '100%', height: '500px', background: 'rgba(10, 15, 25, 0.4)', overflow: 'hidden', borderRadius: '12px', border: '1px solid rgba(255,255,255,0.1)' }}>
      <ForceGraph2D
        ref={fgRef}
        width={dimensions.width}
        height={dimensions.height}
        graphData={graphData}
        nodeLabel={() => ''}
        nodeColor={(node: any) => getGenomeColor(node.genomeType)}
        nodeCanvasObject={paintNode}
        onNodeHover={(node: any) => setHoverNode(node)}
        linkColor={(link: any) => `rgba(255, 255, 255, ${link.similarity * 0.3})`}
        linkWidth={(link: any) => link.similarity * 2}
        enableNodeDrag={true}
        enableZoomInteraction={true}
        enablePanInteraction={true}
        d3AlphaDecay={0.02}
        d3VelocityDecay={0.3}
      />

      {hoverNode && (
        <div style={{
            pointerEvents: 'none',
            position: 'absolute',
            zIndex: 50,
            padding: '16px',
            borderRadius: '12px',
            display: 'flex',
            flexDirection: 'column',
            gap: '8px',
            boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.5)',
            transition: 'all 0.1s ease-out',
            left: (hoverNode.x * (fgRef.current?.zoom() || 1) + (dimensions.width / 2)) + 15,
            top: (hoverNode.y * (fgRef.current?.zoom() || 1) + (dimensions.height / 2)) + 15,
            background: 'rgba(20, 25, 35, 0.6)',
            backdropFilter: 'blur(12px)',
            WebkitBackdropFilter: 'blur(12px)',
            border: '1px solid rgba(255, 255, 255, 0.2)',
            minWidth: '200px',
            color: '#e2e8f0',
            fontFamily: 'sans-serif'
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: '1px solid rgba(255,255,255,0.1)', paddingBottom: '8px', marginBottom: '4px' }}>
            <span style={{ fontWeight: 'bold', color: 'white', fontSize: '14px', wordBreak: 'break-all' }}>{hoverNode.name.substring(0, 15)}...</span>
            <span style={{ fontSize: '11px', padding: '2px 8px', borderRadius: '9999px', fontWeight: 'bold', backgroundColor: 'rgba(255,255,255,0.1)', color: getGenomeColor(hoverNode.genomeType) }}>
              {hoverNode.genomeType.substring(0, 10)}
            </span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px' }}>
            <span style={{ color: '#94a3b8' }}>Fitness:</span>
            <span style={{ color: 'white', fontFamily: 'monospace' }}>{(hoverNode.fitness * 100).toFixed(1)}%</span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px' }}>
            <span style={{ color: '#94a3b8' }}>Generation:</span>
            <span style={{ color: 'white', fontFamily: 'monospace' }}>{hoverNode.generation}</span>
          </div>
        </div>
      )}
      
      <div style={{ position: 'absolute', top: '24px', left: '24px', pointerEvents: 'none' }}>
        <h3 style={{ fontSize: '18px', fontWeight: 'bold', color: 'white', margin: 0, letterSpacing: '-0.025em' }}>Swarm Constellation</h3>
        <p style={{ fontSize: '13px', color: '#94a3b8', marginTop: '4px' }}>Population: {graphData.nodes.length} Genomes</p>
      </div>
    </div>
  );
}
