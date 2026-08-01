import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import ForceGraph2D from 'react-force-graph-2d';

export function OrganismConstellation({ population = [] }: { population?: any[] }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const fgRef = useRef<any>();
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

  const graphData = useMemo(() => {
    if (!population || population.length === 0) return { nodes: [], links: [] };

    const nodes = population.map((org, index) => ({
      id: org.id || `agent-${index}`,
      name: org.id || `Agent-${index}`,
      fitness: org.fitness || 0.1,
      genomeType: org.genome?.model || org.model || 'qwen3.5-9b',
      generation: org.genome?.generation || 1
    }));

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
    if (type.includes('ornith')) return '#22d3ee'; // Cyber Blue instead of green
    return '#00f2fe'; // Default Neon Blue
  };

  const paintNode = useCallback((node: any, ctx: any, globalScale: number) => {
    const baseColor = getGenomeColor(node.genomeType);
    const isHovered = hoverNode?.id === node.id;
    
    const time = Date.now() / 1000; 
    let hash = 0;
    for (let i = 0; i < String(node.id).length; i++) {
        hash = String(node.id).charCodeAt(i) + ((hash << 5) - hash);
    }
    const offset = Math.abs(hash) % 10;
    
    // Sleek base size
    const baseRadius = 4;
    const ringRadius = baseRadius + 4;
    
    // Outer rotating dashed ring
    ctx.save();
    ctx.translate(node.x, node.y);
    ctx.rotate(time * (hash % 2 === 0 ? 1 : -1) + offset);
    ctx.beginPath();
    ctx.arc(0, 0, ringRadius, 0, 2 * Math.PI, false);
    ctx.setLineDash([2, 4]);
    ctx.lineWidth = 0.5;
    ctx.strokeStyle = baseColor;
    ctx.globalAlpha = isHovered ? 1 : 0.4;
    ctx.stroke();
    ctx.restore();

    // Solid inner core
    ctx.beginPath();
    ctx.arc(node.x, node.y, baseRadius, 0, 2 * Math.PI, false);
    ctx.fillStyle = isHovered ? '#ffffff' : '#04080f'; // Dark core
    ctx.fill();

    // Core border
    ctx.beginPath();
    ctx.arc(node.x, node.y, baseRadius, 0, 2 * Math.PI, false);
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = isHovered ? '#ffffff' : baseColor;
    ctx.shadowColor = baseColor;
    ctx.shadowBlur = isHovered ? 15 : 5;
    ctx.stroke();
    
    ctx.shadowBlur = 0;
    ctx.globalAlpha = 1;

    // Optional: Draw label if highly zoomed or hovered
    if (globalScale > 3 || isHovered) {
      const label = node.name.substring(0, 8);
      const fontSize = 12 / globalScale;
      ctx.font = `${fontSize}px monospace`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillStyle = 'rgba(255, 255, 255, 0.8)';
      ctx.fillText(label, node.x, node.y + ringRadius + (6 / globalScale));
    }

  }, [hoverNode]);

  // Adjust physics settings on load to space out the nodes
  useEffect(() => {
    if (fgRef.current) {
      fgRef.current.d3Force('charge').strength(-200); // Repel strongly
      fgRef.current.d3Force('link').distance(60);     // Longer links
    }
  }, [graphData]);

  return (
    <div ref={containerRef} style={{ position: 'relative', width: '100%', height: '500px', background: 'rgba(10, 15, 25, 0.4)', overflow: 'hidden', borderRadius: '12px', border: '1px solid rgba(255,255,255,0.05)', boxShadow: 'inset 0 0 40px rgba(0,0,0,0.5)' }}>
      {/* Heavy vignette background */}
      <div className="absolute inset-0 z-0 bg-[radial-gradient(ellipse_at_center,_var(--tw-gradient-stops))] from-transparent via-[#04080f]/40 to-[#04080f]/90 pointer-events-none" />
      
      <ForceGraph2D
        ref={fgRef}
        width={dimensions.width}
        height={dimensions.height}
        graphData={graphData}
        nodeLabel={() => ''} // We draw custom labels
        nodeCanvasObject={paintNode}
        onNodeHover={(node: any) => setHoverNode(node)}
        linkColor={(link: any) => {
          const color = getGenomeColor(graphData.nodes.find(n => n.id === link.source.id || n.id === link.source)?.genomeType || '');
          return color;
        }}
        linkWidth={(link: any) => link.similarity * 1.5}
        linkDirectionalParticles={2}
        linkDirectionalParticleWidth={1.5}
        linkDirectionalParticleColor={(link: any) => '#ffffff'}
        linkDirectionalParticleSpeed={0.005}
        enableNodeDrag={true}
        enableZoomInteraction={true}
        enablePanInteraction={true}
        d3AlphaDecay={0.02}
        d3VelocityDecay={0.3}
      />

      {/* Cyberpunk Floating Tooltip */}
      {hoverNode && (
        <div style={{
            pointerEvents: 'none',
            position: 'absolute',
            zIndex: 50,
            padding: '12px',
            display: 'flex',
            flexDirection: 'column',
            gap: '6px',
            transition: 'all 0.1s ease-out',
            left: (hoverNode.x * (fgRef.current?.zoom() || 1) + (dimensions.width / 2)) + 20,
            top: (hoverNode.y * (fgRef.current?.zoom() || 1) + (dimensions.height / 2)) + 20,
            background: 'rgba(4, 8, 15, 0.85)',
            backdropFilter: 'blur(8px)',
            border: `1px solid ${getGenomeColor(hoverNode.genomeType)}`,
            borderLeft: `3px solid ${getGenomeColor(hoverNode.genomeType)}`, // Cyber accent
            minWidth: '220px',
            color: '#e2e8f0',
            fontFamily: 'monospace' // Monospace for high-tech feel
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: '1px solid rgba(255,255,255,0.1)', paddingBottom: '6px', marginBottom: '4px' }}>
            <span style={{ fontWeight: 'bold', color: 'white', fontSize: '13px', textTransform: 'uppercase' }}>{hoverNode.name.substring(0, 15)}</span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '11px' }}>
            <span style={{ color: '#64748b', textTransform: 'uppercase' }}>Genome ID</span>
            <span style={{ color: getGenomeColor(hoverNode.genomeType) }}>{hoverNode.genomeType}</span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '11px' }}>
            <span style={{ color: '#64748b', textTransform: 'uppercase' }}>Fitness</span>
            <span style={{ color: 'white' }}>{(hoverNode.fitness * 100).toFixed(1)}%</span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '11px' }}>
            <span style={{ color: '#64748b', textTransform: 'uppercase' }}>Generation</span>
            <span style={{ color: 'white' }}>{hoverNode.generation}</span>
          </div>
        </div>
      )}
      
      {/* Telemetry Header */}
      <div style={{ position: 'absolute', top: '24px', left: '24px', pointerEvents: 'none', display: 'flex', flexDirection: 'column', gap: '4px' }}>
        <h3 style={{ fontSize: '14px', fontWeight: 'bold', color: '#22d3ee', margin: 0, letterSpacing: '0.1em', textTransform: 'uppercase', display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{ width: '8px', height: '8px', backgroundColor: '#22d3ee', borderRadius: '50%', boxShadow: '0 0 10px #22d3ee' }}></span>
          Swarm Telemetry
        </h3>
        <p style={{ fontSize: '11px', color: '#94a3b8', fontFamily: 'monospace', textTransform: 'uppercase' }}>Network Nodes: {graphData.nodes.length} Active</p>
      </div>
    </div>
  );
}
