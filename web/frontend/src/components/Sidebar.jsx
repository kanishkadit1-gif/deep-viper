function timeAgo(ts) {
  const s = Math.floor((Date.now() - ts) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

const STATUS_DOT = {
  running: "bg-viper-good", paused: "bg-viper-warn", done: "bg-viper-accent",
  aborted: "bg-viper-bad", error: "bg-viper-bad",
};

export default function Sidebar({ sessions, active, onNew, onOpen, onDelete, models, vlm, setVlm, vlmLocked }) {
  return (
    <aside className="w-64 shrink-0 flex flex-col bg-viper-panel border-r border-viper-border">
      {/* Brand */}
      <div className="h-14 flex items-center gap-2.5 px-4 border-b border-viper-border">
        <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-viper-accent to-indigo-600
                        grid place-items-center font-black text-sm shadow-lg shadow-indigo-900/40">V</div>
        <div className="font-semibold tracking-tight text-sm">
          Deep VIPER <span className="text-viper-muted">Co-Pilot</span>
        </div>
      </div>

      {/* New chat */}
      <div className="p-3">
        <button onClick={onNew}
          className="w-full flex items-center justify-center gap-2 rounded-xl py-2.5
                     bg-viper-accent hover:bg-indigo-500 text-white font-medium text-sm
                     transition shadow-lg shadow-indigo-900/30">
          <span className="text-lg leading-none">+</span> New session
        </button>
      </div>

      {/* History */}
      <div className="flex-1 overflow-y-auto px-2">
        <div className="text-[10px] uppercase tracking-wider text-viper-muted px-2 mb-1">History</div>
        {sessions.length === 0 && (
          <div className="text-xs text-viper-muted/60 px-2 py-4">No sessions yet.</div>
        )}
        {sessions.map((s) => {
          const on = active?.id === s.id;
          return (
            <div key={s.id} onClick={() => onOpen(s)} role="button"
              className={`group relative w-full text-left rounded-lg px-2.5 py-2 mb-0.5 flex gap-2.5
                cursor-pointer transition ${on ? "bg-viper-panel2" : "hover:bg-viper-panel2/60"}`}>
              {s.thumb ? (
                <img src={s.thumb} alt="" className="w-9 h-9 rounded object-cover bg-black shrink-0" />
              ) : (
                <div className="w-9 h-9 rounded bg-viper-panel2 shrink-0" />
              )}
              <div className="min-w-0 flex-1">
                <div className="text-xs text-viper-text/90 truncate leading-tight pr-5">{s.goal}</div>
                <div className="flex items-center gap-1.5 mt-0.5">
                  <span className={`w-1.5 h-1.5 rounded-full ${STATUS_DOT[s.status] || "bg-viper-muted"}`} />
                  <span className="text-[10px] text-viper-muted">{timeAgo(s.createdAt)}</span>
                </div>
              </div>
              <button title="Delete session"
                onClick={(e) => { e.stopPropagation();
                  if (confirm("Delete this session?")) onDelete?.(s); }}
                className="absolute top-1.5 right-1.5 opacity-0 group-hover:opacity-100
                           text-viper-muted hover:text-viper-bad text-xs transition">✕</button>
            </div>
          );
        })}
      </div>

      {/* Model selector */}
      <div className="p-3 border-t border-viper-border">
        <div className="text-[10px] uppercase tracking-wider text-viper-muted mb-1.5">Model</div>
        <select value={vlm} onChange={(e) => setVlm(e.target.value)} disabled={vlmLocked}
          className="w-full bg-viper-panel2 border border-viper-border rounded-lg px-2.5 py-2
                     text-sm text-viper-text disabled:opacity-50">
          {models.map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
      </div>
    </aside>
  );
}
