import { useState } from "react";

export default function GoalComposer({ scene, onStart, disabled }) {
  const [goal, setGoal] = useState("");
  const [conflict, setConflict] = useState("p");

  return (
    <div className="p-3 flex-1 flex flex-col min-h-0">
      <div className="text-xs uppercase tracking-wider text-viper-muted mb-2">Goal</div>

      {scene?.objects?.length > 0 && (
        <div className="flex flex-wrap gap-1 mb-2">
          {scene.objects.map((o) => (
            <span key={o.id}
              className="text-[10px] px-1.5 py-0.5 rounded bg-viper-panel2 border border-viper-border text-viper-muted">
              {o.label} <span className="opacity-60">[{o.center.join(",")}]</span>
            </span>
          ))}
        </div>
      )}

      <textarea
        value={goal} onChange={(e) => setGoal(e.target.value)}
        placeholder="e.g. Move the red box to [543, 496]. Then stack the purple box on the yellow box."
        disabled={disabled}
        className="w-full h-28 resize-none rounded-lg bg-viper-panel2 border border-viper-border
                   p-2.5 text-sm placeholder:text-viper-muted/60 focus:outline-none
                   focus:ring-1 focus:ring-viper-accent disabled:opacity-50" />

      {scene?.sample_goals?.length > 0 && (
        <div className="mt-2 space-y-1">
          {scene.sample_goals.slice(0, 3).map((g, i) => (
            <button key={i} onClick={() => setGoal(g)} disabled={disabled}
              className="block w-full text-left text-[11px] text-viper-muted hover:text-viper-text
                         truncate disabled:opacity-50">
              ↳ {g}
            </button>
          ))}
        </div>
      )}

      <div className="mt-auto pt-3 flex items-center gap-2">
        <select value={conflict} onChange={(e) => setConflict(e.target.value)} disabled={disabled}
          title="Default answer for full-overlap conflicts"
          className="bg-viper-panel2 border border-viper-border rounded-md px-2 py-2 text-xs
                     disabled:opacity-50">
          <option value="p">clear blocker</option>
          <option value="s">stack</option>
        </select>
        <button onClick={() => onStart(goal, conflict)} disabled={disabled || !goal.trim()}
          className="flex-1 rounded-lg bg-viper-accent hover:bg-indigo-500 text-white font-medium
                     py-2 text-sm transition disabled:opacity-40 disabled:cursor-not-allowed">
          ▶ Run plan
        </button>
      </div>
    </div>
  );
}
