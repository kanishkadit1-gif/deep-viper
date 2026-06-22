import { useEffect, useRef } from "react";

const META = {
  session_started:   { icon: "🎬", label: "Session",     color: "text-viper-accent" },
  plan_proposed:     { icon: "🧩", label: "Plan",        color: "text-viper-accent" },
  conflict_detected: { icon: "⚠️", label: "Conflict",    color: "text-viper-warn" },
  segment_started:   { icon: "→",  label: "Sub-task",    color: "text-viper-text" },
  explore_iter:      { icon: "🔍", label: "Explore",     color: "text-sky-300" },
  path_locked:       { icon: "🔒", label: "Locked",      color: "text-viper-good" },
  refine_iter:       { icon: "✨", label: "Refine",      color: "text-violet-300" },
  awaiting_input:    { icon: "⏳", label: "Review",      color: "text-viper-warn" },
  path_committed:    { icon: "✅", label: "Committed",   color: "text-viper-good" },
  ik_done:           { icon: "🦾", label: "IK",          color: "text-viper-text" },
  render_progress:   { icon: "🎞️", label: "Render",      color: "text-viper-text" },
  info:              { icon: "·",  label: "Info",        color: "text-viper-muted" },
  session_done:      { icon: "🏁", label: "Done",        color: "text-viper-accent" },
  session_aborted:   { icon: "🛑", label: "Aborted",     color: "text-viper-bad" },
};

export default function Timeline({ events, focus, setFocus, status }) {
  const endRef = useRef(null);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [events.length]);

  if (events.length === 0) {
    return (
      <div className="flex-1 grid place-items-center text-center px-8">
        <div className="text-viper-muted">
          <div className="text-5xl mb-3 opacity-30">⟜</div>
          <div className="text-sm">Pick a scene, write a goal, and hit <b className="text-viper-text">Run plan</b>.</div>
          <div className="text-xs mt-1 opacity-60">The VLM's reasoning will stream here live.</div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto px-5 py-4">
      <div className="relative max-w-3xl mx-auto">
        <div className="absolute left-[15px] top-1 bottom-1 w-px bg-viper-border" />
        {events.map((e, i) => (
          <Card key={i} e={e} active={focus === e} onClick={() => setFocus(e)} />
        ))}
        <div ref={endRef} />
      </div>
    </div>
  );
}

function Card({ e, active, onClick }) {
  const m = META[e.type] || META.info;
  const metrics = e.payload?.metrics;
  return (
    <button onClick={onClick}
      className={`group relative w-full text-left pl-10 pr-3 py-2 mb-1.5 rounded-lg
        animate-slidein transition ${active ? "bg-viper-panel2" : "hover:bg-viper-panel/60"}`}>
      <span className="absolute left-1 top-2 w-7 h-7 grid place-items-center rounded-full
                       bg-viper-panel border border-viper-border text-sm">{m.icon}</span>
      <div className="flex items-center gap-2">
        <span className={`text-[11px] font-semibold uppercase tracking-wide ${m.color}`}>{m.label}</span>
        {e.payload?.phase && (
          <span className="text-[10px] text-viper-muted">{e.payload.phase}</span>
        )}
        {typeof e.payload?.risk === "number" && (
          <RiskPill risk={e.payload.risk} />
        )}
        {metrics?.num_waypoints != null && (
          <span className="text-[10px] text-viper-muted">
            {metrics.num_waypoints} wp · {Math.round(metrics.length_px)}px
          </span>
        )}
        {e.payload?.adopted === true && (
          <span className="text-[10px] text-viper-good">adopted</span>
        )}
      </div>
      <div className="text-sm text-viper-text/90 leading-snug">{e.message}</div>
      {e.image_path && (
        <div className="mt-1 text-[10px] text-viper-accent/70 group-hover:text-viper-accent">
          🖼 view frame →
        </div>
      )}
    </button>
  );
}

function RiskPill({ risk }) {
  const c = risk < 0.2 ? "bg-viper-good/15 text-viper-good"
          : risk < 0.5 ? "bg-viper-warn/15 text-viper-warn"
          : "bg-viper-bad/15 text-viper-bad";
  return <span className={`text-[10px] px-1.5 py-0.5 rounded ${c}`}>risk {risk.toFixed(2)}</span>;
}
