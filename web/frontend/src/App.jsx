import { useEffect, useRef, useState } from "react";
import {
  getModels, startSession, openSessionSocket, sendAction,
  sendMessage, getSessionEvents, renderVideo,
} from "./api";
import Sidebar from "./components/Sidebar";
import NewSession from "./components/NewSession";
import Session from "./components/Session";
import { loadSessions, saveSession } from "./sessionStore";

export default function App() {
  const [models, setModels] = useState(["openai"]);
  const [vlm, setVlm] = useState("openai");

  const [sessions, setSessions] = useState([]);
  const [active, setActive] = useState(null);
  const [composing, setComposing] = useState(true);

  const [status, setStatus] = useState("idle");
  const [events, setEvents] = useState([]);
  const wsRef = useRef(null);

  useEffect(() => {
    getModels().then((m) => { setModels(m); setVlm(m[0]); });
    setSessions(loadSessions());
  }, []);

  function pushEvent(evt) { setEvents((prev) => [...prev, evt]); }

  async function handleStart(form) {
    setComposing(false);
    setEvents([]); setStatus("running");
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
    openWs(rec);
  }

  function openWs(rec) {
    wsRef.current = openSessionSocket(rec.sessionId, {
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

  // The single chat input. Routes by session state on the backend.
  async function say(text) {
    if (!active?.sessionId || !text.trim()) return;
    // Echo the user's message into the timeline immediately.
    pushEvent({ type: "user_message", message: text, ts: Date.now() / 1000 });
    const res = await sendMessage(active.sessionId, text);
    if (res?.status) setStatus(res.status === "awaiting" ? "running" : res.status);
  }

  function control(a) {
    sendAction(wsRef.current, a);
    if (a === "pause") setStatus("paused");
    if (a === "stop") setStatus("aborted");
  }

  function approve() { sendAction(wsRef.current, "approve"); }

  function editWaypoints(waypoints) {
    if (wsRef.current) wsRef.current.send(JSON.stringify({ action: "override", override: waypoints }));
  }

  async function generateVideo() {
    if (active?.sessionId) { openWs(active); await renderVideo(active.sessionId); }
  }

  function newChat() {
    setComposing(true); setActive(null); setEvents([]); setStatus("idle");
    if (wsRef.current) { try { wsRef.current.close(); } catch {} }
  }

  async function openPast(rec) {
    setComposing(false); setActive(rec); setStatus(rec.status || "done");
    setEvents(rec.events || []);
    if (rec.sessionId) {
      const data = await getSessionEvents(rec.sessionId);
      if (data?.events?.length) { setEvents(data.events); setStatus(data.status || "done"); }
    }
  }

  return (
    <div className="h-full flex bg-viper-bg text-viper-text">
      <Sidebar sessions={sessions} active={active} onNew={newChat} onOpen={openPast}
               models={models} vlm={vlm} setVlm={setVlm}
               vlmLocked={status === "running" || status === "paused"} />
      <div className="flex-1 min-w-0 flex flex-col">
        {composing ? (
          <NewSession onStart={handleStart} />
        ) : (
          <Session events={events} status={status} scene={active?.scene} goal={active?.goal}
                   onSay={say} onApprove={approve} onControl={control}
                   onEdit={editWaypoints} onRenderVideo={generateVideo} />
        )}
      </div>
    </div>
  );
}
