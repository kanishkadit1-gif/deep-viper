import { useState } from "react";

export default function Controls({ status, running, onAction }) {
  const [hint, setHint] = useState("");

  function sendCorrection() {
    if (!hint.trim()) return;
    onAction("correction", hint.trim());
    setHint("");
  }

  return (
    <div className="mt-auto p-3 border-t border-viper-border">
      {/* Correction box — the harness-coaching feature */}
      <div className="text-xs uppercase tracking-wider text-viper-muted mb-2">Coach the VLM</div>
      <div className="flex gap-2">
        <input
          value={hint} onChange={(e) => setHint(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && sendCorrection()}
          placeholder="e.g. route along the right side, fewer waypoints…"
          disabled={!running}
          className="flex-1 rounded-lg bg-viper-panel2 border border-viper-border px-3 py-2 text-sm
                     placeholder:text-viper-muted/60 focus:outline-none focus:ring-1
                     focus:ring-viper-accent disabled:opacity-50" />
        <button onClick={sendCorrection} disabled={!running || !hint.trim()}
          className="rounded-lg px-3 py-2 text-sm font-medium bg-violet-600 hover:bg-violet-500
                     text-white transition disabled:opacity-40">
          Send
        </button>
      </div>
      <div className="text-[10px] text-viper-muted mt-1">
        Injected into the next proposal &amp; remembered for this obstacle.
      </div>

      {/* Transport controls */}
      <div className="flex gap-2 mt-3">
        {status === "paused" ? (
          <Btn onClick={() => onAction("continue")} disabled={!running} className="bg-viper-good/20 text-viper-good">
            ▶ Resume
          </Btn>
        ) : (
          <Btn onClick={() => onAction("pause")} disabled={!running} className="bg-viper-warn/15 text-viper-warn">
            ⏸ Pause
          </Btn>
        )}
        <Btn onClick={() => onAction("stop")} disabled={!running} className="bg-viper-bad/15 text-viper-bad">
          ⏹ Stop
        </Btn>
      </div>
    </div>
  );
}

function Btn({ children, className = "", ...p }) {
  return (
    <button {...p}
      className={`flex-1 rounded-lg py-2 text-sm font-medium transition
                  disabled:opacity-40 disabled:cursor-not-allowed ${className}`}>
      {children}
    </button>
  );
}
