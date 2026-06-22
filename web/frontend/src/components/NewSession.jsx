import { useEffect, useState } from "react";
import { getScenes, uploadScene } from "../api";

export default function NewSession({ onStart }) {
  const [mode, setMode] = useState("upload");   // upload | existing
  const [scenes, setScenes] = useState([]);
  const [scene, setScene] = useState(null);     // resolved scene (dataset_path + image)
  const [goal, setGoal] = useState("");
  const [conflict, setConflict] = useState("p");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  // upload state
  const [image, setImage] = useState(null);
  const [dataset, setDataset] = useState(null);
  const [blend, setBlend] = useState(null);
  const [preview, setPreview] = useState(null);

  useEffect(() => { getScenes().then(setScenes); }, []);
  useEffect(() => {
    if (!image) return setPreview(null);
    const url = URL.createObjectURL(image); setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [image]);

  async function resolveAndStart() {
    setErr(null); setBusy(true);
    try {
      let s = scene;
      if (mode === "upload") {
        if (!image || !dataset) { setErr("Scene image and dataset.json are required."); setBusy(false); return; }
        s = await uploadScene({ image, dataset, blend });
      }
      if (!s) { setErr("Pick or upload a scene."); setBusy(false); return; }
      if (!goal.trim()) { setErr("Write a goal."); setBusy(false); return; }
      onStart({ scene: s, goal: goal.trim(), conflict_default: conflict });
    } catch (e) {
      setErr(String(e.message || e));
    } finally { setBusy(false); }
  }

  const canStart = goal.trim() && (mode === "existing" ? scene : image && dataset);

  return (
    <div className="flex-1 overflow-y-auto grid place-items-center p-8">
      <div className="w-full max-w-xl animate-slidein">
        <h1 className="text-2xl font-semibold tracking-tight mb-1">Start a new session</h1>
        <p className="text-sm text-viper-muted mb-6">
          Bring your own scene. Upload an image + its dataset; add a <code>.blend</code> for full
          robot-arm simulation (optional — without it you still get the animated trajectory).
        </p>

        {/* Mode toggle */}
        <div className="inline-flex p-1 rounded-xl bg-viper-panel border border-viper-border mb-5 text-sm">
          {["upload", "existing"].map((m) => (
            <button key={m} onClick={() => setMode(m)}
              className={`px-4 py-1.5 rounded-lg transition capitalize ${
                mode === m ? "bg-viper-accent text-white" : "text-viper-muted hover:text-viper-text"}`}>
              {m === "upload" ? "Upload scene" : "Use existing"}
            </button>
          ))}
        </div>

        {mode === "upload" ? (
          <div className="grid grid-cols-3 gap-3 mb-5">
            <Drop label="Scene image" hint="PNG/JPG · required" accept="image/*"
                  file={image} onFile={setImage} preview={preview} />
            <Drop label="dataset.json" hint="objects + camera · required" accept=".json"
                  file={dataset} onFile={setDataset} />
            <Drop label=".blend" hint="for simulation · optional" accept=".blend"
                  file={blend} onFile={setBlend} optional />
          </div>
        ) : (
          <div className="grid grid-cols-3 gap-2 mb-5 max-h-52 overflow-y-auto pr-1">
            {scenes.map((s) => {
              const on = scene?.dataset_path === s.dataset_path;
              return (
                <button key={s.id} onClick={() => setScene(s)}
                  className={`rounded-lg overflow-hidden border text-left transition ${
                    on ? "border-viper-accent ring-1 ring-viper-accent" : "border-viper-border hover:border-viper-muted"}`}>
                  <img src={s.image} alt={s.id} className="w-full h-16 object-cover bg-black" />
                  <div className="px-2 py-1 text-[10px]">
                    <div className="truncate">{s.id}</div>
                    <div className="text-viper-muted">{s.num_objects} obj · {s.is_3d ? "3D" : "2D"}</div>
                  </div>
                </button>
              );
            })}
          </div>
        )}

        {/* Goal */}
        <label className="text-xs uppercase tracking-wider text-viper-muted">Goal</label>
        <textarea value={goal} onChange={(e) => setGoal(e.target.value)}
          placeholder="e.g. Move the red box to [543, 496]. Then stack the purple box on the yellow box."
          className="mt-1.5 w-full h-24 resize-none rounded-xl bg-viper-panel border border-viper-border
                     p-3 text-sm placeholder:text-viper-muted/60 focus:outline-none focus:ring-1
                     focus:ring-viper-accent" />

        {err && <div className="mt-3 text-sm text-viper-bad">{err}</div>}

        <div className="mt-5 flex items-center gap-3">
          <select value={conflict} onChange={(e) => setConflict(e.target.value)}
            className="bg-viper-panel border border-viper-border rounded-lg px-3 py-2.5 text-sm">
            <option value="p">conflict → clear blocker</option>
            <option value="s">conflict → stack</option>
          </select>
          <button onClick={resolveAndStart} disabled={!canStart || busy}
            className="flex-1 rounded-xl bg-viper-accent hover:bg-indigo-500 text-white font-medium
                       py-2.5 text-sm transition disabled:opacity-40 disabled:cursor-not-allowed
                       flex items-center justify-center gap-2">
            {busy ? "Preparing…" : <>▶ Start planning</>}
          </button>
        </div>
      </div>
    </div>
  );
}

function Drop({ label, hint, accept, file, onFile, preview, optional }) {
  return (
    <label className={`relative rounded-xl border-2 border-dashed cursor-pointer transition
      grid place-items-center text-center p-3 h-28 overflow-hidden
      ${file ? "border-viper-accent/60 bg-viper-accent/5" : "border-viper-border hover:border-viper-muted bg-viper-panel"}`}>
      <input type="file" accept={accept} className="hidden"
        onChange={(e) => onFile(e.target.files?.[0] || null)} />
      {preview ? (
        <img src={preview} alt="" className="absolute inset-0 w-full h-full object-cover opacity-80" />
      ) : null}
      <div className="relative">
        <div className="text-xs font-medium">{label}{optional && <span className="text-viper-muted"> ·opt</span>}</div>
        <div className="text-[10px] text-viper-muted mt-0.5">{hint}</div>
        {file && <div className="text-[10px] text-viper-good mt-1 truncate max-w-[120px]">✓ {file.name}</div>}
      </div>
    </label>
  );
}
