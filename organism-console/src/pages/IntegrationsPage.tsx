export default function IntegrationsPage() {
  const providers = [
    { name: "OpenAI", status: "online", type: "LLM" },
    { name: "Anthropic", status: "online", type: "LLM" },
    { name: "llamacpp", status: "online", type: "Local" },
    { name: "GitHub", status: "offline", type: "Tools" },
    { name: "Slack", status: "offline", type: "Communication" }
  ] as const

  return (
    <section className="flex flex-col h-full w-full overflow-hidden p-6 text-slate-300">
      <header className="flex flex-col gap-2 bg-[#04080f]/60 border border-white/5 backdrop-blur-xl p-6 rounded-2xl mb-6 shadow-[0_0_30px_rgba(0,0,0,0.5)] shrink-0">
        <div className="flex items-center gap-2 text-[11px] font-black uppercase tracking-widest text-cyan-400">
          <span className="w-1.5 h-1.5 rounded-full bg-cyan-400 shadow-[0_0_10px_#22d3ee]" />
          Integration surface
        </div>
        <h1 className="text-3xl font-black text-white m-0">Integrations</h1>
        <p className="text-sm text-slate-400 m-0">Manage provider connections, API keys, and external system adapters.</p>
      </header>

      <div className="flex flex-col gap-6 overflow-y-auto custom-scrollbar pb-10">
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {providers.map((p) => (
            <article
              key={p.name}
              className={`relative overflow-hidden rounded-2xl p-5 border shadow-lg backdrop-blur-md transition-all ${
                p.status === "online" 
                  ? "bg-cyan-900/20 border-cyan-500/30 shadow-[0_16px_40px_rgba(0,0,0,0.2)]" 
                  : "bg-slate-900/40 border-white/5 shadow-[0_12px_32px_rgba(0,0,0,0.16)]"
              }`}
            >
              <div
                className="absolute top-0 left-0 w-full h-px opacity-80 pointer-events-none"
                style={{
                  background: p.status === "online"
                    ? "linear-gradient(90deg, #22d3ee, transparent 72%)"
                    : "linear-gradient(90deg, rgba(255,255,255,0.12), transparent 72%)"
                }}
              />

              <div className="flex justify-between items-start gap-3">
                <div className="flex flex-col gap-2">
                  <div className="flex items-center gap-2.5">
                    <span
                      className={`w-2.5 h-2.5 rounded-full shrink-0 ${
                        p.status === "online" ? "bg-emerald-400 shadow-[0_0_14px_rgba(52,211,153,0.45)]" : "bg-slate-600"
                      }`}
                    />
                    <div className="text-lg font-black text-white leading-tight">{p.name}</div>
                  </div>

                  <div className="inline-flex items-center gap-2 px-2.5 py-1 rounded-full bg-white/5 border border-white/10 text-[10px] font-black uppercase tracking-widest text-slate-400 w-fit">
                    {p.type} provider
                  </div>
                </div>

                <span
                  className={`px-3 py-1 rounded-full text-[10px] font-black uppercase tracking-widest border ${
                    p.status === "online"
                      ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-400"
                      : "bg-slate-800/50 border-slate-600/50 text-slate-500"
                  }`}
                >
                  {p.status}
                </span>
              </div>

              <div className="mt-4 pt-3 border-t border-white/10 text-[13px] text-slate-400 leading-relaxed">
                {p.status === "online"
                  ? `Connection to ${p.name} is visible to the console and ready for operator review.`
                  : `${p.name} is configured as an available surface but is not currently broadcasting.`}
              </div>
            </article>
          ))}
        </div>

        <article className="relative overflow-hidden rounded-2xl p-6 bg-slate-900/50 border border-white/10 backdrop-blur-md">
          <div className="inline-flex items-center gap-2.5 px-3 py-1.5 rounded-full bg-cyan-500/10 border border-cyan-500/20 text-cyan-400 text-[11px] font-black uppercase tracking-[0.1em] mb-4">
            <span className="w-2 h-2 rounded-full bg-cyan-400 shadow-[0_0_14px_rgba(34,211,238,0.6)]" />
            Adapter staging area
          </div>

          <h2 className="text-xl font-bold text-white m-0">Connected system surface</h2>

          <div className="mt-4 p-8 text-center rounded-2xl border border-dashed border-slate-600/50 bg-gradient-to-b from-white/5 to-transparent shadow-[inset_0_1px_0_rgba(255,255,255,0.03)]">
            <div className="w-12 h-12 mx-auto mb-4 rounded-xl flex items-center justify-center bg-white/5 border border-white/10 text-cyan-400 text-2xl shadow-[0_10px_30px_rgba(0,0,0,0.16)]">
              +
            </div>

            <p className="text-slate-300 font-bold m-0 leading-relaxed">
              No external adapters are currently broadcasting.
            </p>

            <p className="text-slate-500 text-sm mt-2 max-w-[520px] mx-auto leading-relaxed">
              Bring a new provider online to expand the organism's action surface and external system reach.
            </p>

            <button className="mt-6 px-6 py-2.5 bg-gradient-to-b from-white/10 to-cyan-500 text-black font-black uppercase tracking-widest rounded-full border border-white/20 shadow-[0_8px_24px_rgba(34,211,238,0.3)] hover:shadow-[0_8px_30px_rgba(34,211,238,0.5)] transition-all">
              Add new integration
            </button>
          </div>
        </article>
      </div>
    </section>
  )
}

