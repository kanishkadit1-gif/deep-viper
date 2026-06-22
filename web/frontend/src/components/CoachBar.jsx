import { useState } from "react";

export default function CoachBar({ status, running, onAction }) {
  const [hint, setHint] = useState("");
  const send = () => { if (hint.trim()) { onAction("correction", hint.trim()); setHint(""); } };

  return (
    <div className="shrink-0 border-t border-viper-border bg-viper-panel/60 backdrop-blur px-6 py-3
                    flex items-center gap-3">
      {/* Coach input */}
      <div className="flex-1 flex items-center gap-2 rounded-xl bg-viper-panel2 border border-viper-border
                      px-3 focus-within:ring-1 focus-within:ring-violet-500">
        <span className="text-violet-300 text-sm">✦</span>
        <input value={hint} onChange={(e) => setHint(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          placeholder={running ? "Coach the VLM — e.g. route along the right side, fewer waypoints…"
                               : "Start a session to coach the VLM"}
          disabled={!running}
          className="flex-1 bg-transparent py-2.5 text-sm placeholder:text-viper-muted/60
                     focus:outline-none disabled:opacity-50" />
        <button onClick={send} disabled={!running || !hint.trim()}
          className="text-sm font-medium text-violet-300 hover:text-violet-200 disabled:opacity-40 px-1">
          Send
        </button>
      </div>

      {/* Transport */}
      {status === "paused" ? (
        <Btn onClick={() => onAction("continue")} disabled={!running} cls="bg-viper-good/20 text-viper-good">▶ Resume</Btn>
      ) : (
        <Btn onClick={() => onAction("pause")} disabled={!running} cls="bg-viper-warn/15 text-viper-warn">⏸ Pause</Btn>
      )}
      <Btn onClick={() => onAction("stop")} disabled={!running} cls="bg-viper-bad/15 text-viper-bad">⏹ Stop</Btn>
    </div>
  );
}

function Btn({ children, cls = "", ...p }) {
  return (
    <button {...p}
      className={`rounded-xl px-4 py-2.5 text-sm font-medium transition shrink-0
                  disabled:opacity-40 disabled:cursor-not-allowed ${cls}`}>
      {children}
    </button>
  );
}
