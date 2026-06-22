import { useEffect, useRef, useState } from "react";
import { getScenes, getModels, startSession, openSessionSocket, sendAction } from "./api";
import ScenePicker from "./components/ScenePicker";
import GoalComposer from "./components/GoalComposer";
import Timeline from "./components/Timeline";
import StageViewer from "./components/StageViewer";
import Controls from "./components/Controls";

export default function App() {
  const [scenes, setScenes] = useState([]);
  const [models, setModels] = useState(["openai"]);
  const [scene, setScene] = useState(null);
  const [vlm, setVlm] = useState("openai");

  const [sessionId, setSessionId] = useState(null);
  const [status, setStatus] = useState("idle"); // idle|running|paused|done|aborted|error
  const [events, setEvents] = useState([]);
  const [focus, setFocus] = useState(null); // event currently shown in the viewer
  const wsRef = useRef(null);

  useEffect(() => {
    getScenes().then((s) => { setScenes(s); if (s[0]) setScene(s[0]); });
    getModels().then((m) => { setModels(m); setVlm(m[0]); });
  }, []);

  function pushEvent(evt) {
    setEvents((prev) => [...prev, evt]);
    if (evt.image_path) setFocus(evt); // auto-focus events with a fresh image
  }

  async function handleStart(goal, conflictDefault) {
    if (!scene || !goal.trim()) return;
    setEvents([]); setFocus(null); setStatus("running");
    const res = await startSession({
      goal, dataset_path: scene.dataset_path, vlm, conflict_default: conflictDefault,
    });
    setSessionId(res.session_id);
    wsRef.current = openSessionSocket(res.session_id, {
      onEvent: pushEvent,
      onEnd: (s) => setStatus(s),
    });
  }

  function action(a, text) {
    sendAction(wsRef.current, a, text);
    if (a === "pause") setStatus("paused");
    if (a === "continue") setStatus("running");
    if (a === "stop") setStatus("aborted");
  }

  const running = status === "running" || status === "paused";

  return (
    <div className="h-full flex flex-col bg-viper-bg text-viper-text">
      <Header status={status} vlm={vlm} models={models} setVlm={setVlm} disabled={running} />

      <div className="flex-1 min-h-0 grid grid-cols-[300px_1fr_minmax(360px,460px)] gap-px bg-viper-border">
        <aside className="bg-viper-panel flex flex-col min-h-0">
          <ScenePicker scenes={scenes} scene={scene} setScene={setScene} disabled={running} />
          <GoalComposer scene={scene} onStart={handleStart} disabled={running || !scene} />
        </aside>

        <main className="bg-viper-bg min-h-0 flex flex-col">
          <Timeline events={events} focus={focus} setFocus={setFocus} status={status} />
        </main>

        <section className="bg-viper-panel flex flex-col min-h-0">
          <StageViewer event={focus} scene={scene} />
          <Controls status={status} running={running} onAction={action} />
        </section>
      </div>
    </div>
  );
}

function Header({ status, vlm, models, setVlm, disabled }) {
  const dot = {
    idle: "bg-viper-muted", running: "bg-viper-good animate-pulseDot",
    paused: "bg-viper-warn", done: "bg-viper-accent",
    aborted: "bg-viper-bad", error: "bg-viper-bad",
  }[status] || "bg-viper-muted";
  return (
    <header className="h-14 shrink-0 flex items-center justify-between px-5
                       bg-viper-panel border-b border-viper-border">
      <div className="flex items-center gap-3">
        <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-viper-accent to-indigo-600
                        grid place-items-center font-black text-sm">V</div>
        <div className="font-semibold tracking-tight">
          Deep VIPER <span className="text-viper-muted">Co-Pilot</span>
        </div>
        <span className={`ml-2 w-2 h-2 rounded-full ${dot}`} />
        <span className="text-xs text-viper-muted capitalize">{status}</span>
      </div>
      <div className="flex items-center gap-2 text-sm">
        <span className="text-viper-muted">Model</span>
        <select value={vlm} onChange={(e) => setVlm(e.target.value)} disabled={disabled}
          className="bg-viper-panel2 border border-viper-border rounded-md px-2 py-1
                     text-viper-text disabled:opacity-50">
          {models.map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
      </div>
    </header>
  );
}
