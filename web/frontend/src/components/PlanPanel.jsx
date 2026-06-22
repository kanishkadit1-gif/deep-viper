import { useState } from "react";

const OP_META = {
  move_to: { icon: "→", color: "text-sky-300", label: "move to" },
  pick:    { icon: "✊", color: "text-viper-warn", label: "pick" },
  place:   { icon: "📍", color: "text-viper-good", label: "place" },
};

/** Plan-approval overlay: shows the subtask plan; approve / refine / cancel. */
export default function PlanPanel({ plan, numConflicts, onApprove, onRefine, onCancel }) {
  const [hint, setHint] = useState("");
  const refine = () => { if (hint.trim()) { onRefine(hint.trim()); setHint(""); } };

  return (
    <div className="absolute inset-0 z-10 grid place-items-center bg-viper-bg/70 backdrop-blur-sm p-6">
      <div className="w-full max-w-lg rounded-2xl border border-viper-border bg-viper-panel
                      shadow-2xl shadow-black/60 animate-slidein overflow-hidden">
        <div className="px-5 py-4 border-b border-viper-border flex items-center justify-between">
          <div>
            <div className="font-semibold">Verify the plan</div>
            <div className="text-xs text-viper-muted">
              {plan.length} steps{numConflicts > 0 && ` · ${numConflicts} conflict(s) auto-resolved`}
            </div>
          </div>
          <span className="text-xs px-2 py-1 rounded-full bg-viper-warn/15 text-viper-warn">awaiting approval</span>
        </div>

        <div className="max-h-72 overflow-y-auto p-3">
          {plan.map((s) => {
            const m = OP_META[s.op] || { icon: "·", color: "text-viper-muted", label: s.op };
            return (
              <div key={s.step} className="flex items-center gap-3 px-2 py-1.5 rounded-lg hover:bg-viper-panel2/60">
                <span className="w-6 text-center text-xs text-viper-muted">{s.step}</span>
                <span className={`w-6 text-center ${m.color}`}>{m.icon}</span>
                <span className={`text-sm font-medium ${m.color}`}>{m.label}</span>
                <span className="text-sm text-viper-text/80">
                  target {s.args?.target_id}
                  {s.args?.destination && <span className="text-viper-muted"> → [{s.args.destination.join(", ")}]</span>}
                  {s.stack_onto && <span className="text-viper-good"> (stack on {s.stack_onto})</span>}
                </span>
              </div>
            );
          })}
        </div>

        <div className="p-3 border-t border-viper-border space-y-2">
          <div className="flex items-center gap-2 rounded-xl bg-viper-panel2 border border-viper-border px-3
                          focus-within:ring-1 focus-within:ring-viper-accent">
            <span className="text-viper-accent text-sm">✦</span>
            <input value={hint} onChange={(e) => setHint(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && refine()}
              placeholder="Refine the plan — e.g. move the blue box first, or use a different spot"
              className="flex-1 bg-transparent py-2 text-sm placeholder:text-viper-muted/60 focus:outline-none" />
            <button onClick={refine} disabled={!hint.trim()}
              className="text-sm font-medium text-viper-accent hover:text-indigo-300 disabled:opacity-40 px-1">
              Refine
            </button>
          </div>
          <div className="flex gap-2">
            <button onClick={onApprove}
              className="flex-1 rounded-xl bg-viper-good/20 text-viper-good hover:bg-viper-good/30
                         font-medium py-2.5 text-sm transition">✓ Approve &amp; run</button>
            <button onClick={onCancel}
              className="rounded-xl bg-viper-bad/15 text-viper-bad hover:bg-viper-bad/25
                         font-medium py-2.5 px-4 text-sm transition">Cancel</button>
          </div>
        </div>
      </div>
    </div>
  );
}
