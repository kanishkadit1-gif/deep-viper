import { useEffect, useRef, useState } from "react";
import { getModels, startSession, openSessionSocket, sendAction, getSessionEvents } from "./api";
import Sidebar from "./components/Sidebar";
import NewSession from "./components/NewSession";
import Stage from "./components/Stage";
import CoachBar from "./components/CoachBar";
import { loadSessions, saveSession } from "./sessionStore";

export default function App() {
  const [models, setModels] = useState(["openai"]);
  const [vlm, setVlm] = useState("openai");

  const [sessions, setSessions] = useState([]);   // history
  const [active, setActive] = useState(null);      // active session record
  const [composing, setComposing] = useState(true); // showing the new-session panel

  const [status, setStatus] = useState("idle");
  const [events, setEvents] = useState([]);
  const [cursor, setCursor] = useState(-1);  // which step is shown on the stage (-1 = latest)
  const wsRef = useRef(null);

  useEffect(() => {
    getModels().then((m) => { setModels(m); setVlm(m[0]); });
    setSessions(loadSessions());
  }, []);

  function pushEvent(evt) {
    setEvents((prev) => {
      const next = [...prev, evt];
      return next;
    });
    setCursor(-1); // follow live
  }

  async function handleStart(form) {
    setComposing(false);
    setEvents([]); setCursor(-1); setStatus("running");
    const rec = {
      id: crypto.randomUUID().slice(0, 8),
      goal: form.goal, scene: form.scene, vlm,
      createdAt: Date.now(), thumb: form.scene?.image || null,
    };
    setActive(rec);

    const res = await startSession({
      goal: form.goal, dataset_path: form.scene.dataset_path,
      vlm, conflict_default: form.conflict_default,
    });
    rec.sessionId = res.session_id;

    wsRef.current = openSessionSocket(res.session_id, {
      onEvent: pushEvent,
      onEnd: (s) => {
        setStatus(s);
        setEvents((evs) => {
          const saved = { ...rec, status: s, events: evs };
          setSessions((prev) => [saved, ...prev.filter((x) => x.id !== rec.id)]);
          saveSession(saved);
          return evs;
        });
      },
    });
  }

  function action(a, text) {
    sendAction(wsRef.current, a, text);
    if (a === "pause") setStatus("paused");
    if (a === "continue") setStatus("running");
    if (a === "stop") setStatus("aborted");
  }

  function newChat() {
    setComposing(true); setActive(null); setEvents([]); setStatus("idle"); setCursor(-1);
    if (wsRef.current) { try { wsRef.current.close(); } catch {} }
  }

  async function openPast(rec) {
    // Reopen a finished session: prefer server-side recorded events (full replay),
    // fall back to whatever was cached locally.
    setComposing(false); setActive(rec); setStatus(rec.status || "done"); setCursor(-1);
    setEvents(rec.events || []);
    if (rec.sessionId) {
      const data = await getSessionEvents(rec.sessionId);
      if (data?.events?.length) { setEvents(data.events); setStatus(data.status || "done"); }
    }
  }

  const running = status === "running" || status === "paused";

  return (
    <div className="h-full flex bg-viper-bg text-viper-text">
      <Sidebar sessions={sessions} active={active} onNew={newChat} onOpen={openPast}
               models={models} vlm={vlm} setVlm={setVlm} vlmLocked={running} />

      <div className="flex-1 min-w-0 flex flex-col">
        {composing ? (
          <NewSession onStart={handleStart} />
        ) : (
          <>
            <Stage events={events} status={status} cursor={cursor} setCursor={setCursor}
                   scene={active?.scene} goal={active?.goal} />
            <CoachBar status={status} running={running} onAction={action} />
          </>
        )}
      </div>
    </div>
  );
}
