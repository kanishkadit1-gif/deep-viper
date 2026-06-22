import { useEffect, useMemo, useRef, useState } from "react";
import { imageUrl } from "../api";
import TrajectoryOverlay from "./TrajectoryOverlay";

/**
 * One session = one conversation. A big visual stage (scene / live trajectory
 * overlay / GIF / video) on top, a scrolling message feed, and ONE always-on
 * chat input at the bottom. The input is never disabled while a session exists;
 * its meaning is routed by session state on the backend (approve / refine /
 * coach). The plan appears inline as a message with an Approve button.
 */
export default function Session({ events, status, scene, goal, onSay, onApprove,
                                  onControl, onEdit, onRenderVideo }) {
  // --- derive the current visual for the stage ---
  const geoEvt = useMemo(
    () => [...events].reverse().find((e) => e.payload?.geometry), [events]);
  const gifEvt = useMemo(
    () => [...events].reverse().find(
      (e) => e.type === "session_done" && e.image_path?.endsWith(".gif")), [events]);
  const videoEvt = useMemo(
    () => [...events].reverse().find((e) => e.payload?.video), [events]);

  const running = status === "running" || status === "paused";
  // Is the backend waiting for a plan decision? (last plan_proposed, nothing executed after)
  const awaitingPlan = useMemo(() => {
    const lastPlan = [...events].reverse().find((e) => e.type === "plan_proposed");
    if (!lastPlan) return null;
    const after = events.slice(events.indexOf(lastPlan) + 1);
    const moved = after.some((e) =>
      ["segment_started", "session_done", "session_aborted"].includes(e.type));
    return moved ? null : lastPlan.payload;
  }, [events]);

  const geo = geoEvt?.payload?.geometry;
  const editable = running && geo && geoEvt?.type === "awaiting_input";

  return (
    <div className="flex-1 min-h-0 flex flex-col">
      {/* Goal header */}
      <div className="h-14 shrink-0 flex items-center justify-between px-6 border-b border-viper-border">
        <div className="min-w-0">
          <div className="text-[10px] uppercase tracking-wider text-viper-muted">Goal</div>
          <div className="text-sm text-viper-text/90 truncate">{goal}</div>
        </div>
        <Transport status={status} running={running} onControl={onControl} />
      </div>

      <div className="flex-1 min-h-0 grid grid-cols-[1fr_380px]">
        {/* Stage */}
        <div className="min-h-0 flex flex-col items-center justify-center p-6 gap-3">
          <div className={`relative w-full max-w-4xl rounded-2xl overflow-hidden border bg-black
                          shadow-2xl shadow-black/50 grid place-items-center transition
                          ${editable ? "border-viper-accent ring-1 ring-viper-accent/40" : "border-viper-border"}`}
               style={{ aspectRatio: "16/9" }}>
            {videoEvt?.payload?.video ? (
              <video controls autoPlay loop className="w-full h-full object-contain"
                     src={`/api/video?path=${encodeURIComponent(videoEvt.payload.video)}`} />
            ) : geo ? (
              <TrajectoryOverlay sceneUrl={geo.scene_image ? imageUrl(geo.scene_image) : scene?.image}
                                 geometry={geo} editable={editable} onEdit={onEdit} />
            ) : gifEvt ? (
              <img src={imageUrl(gifEvt.image_path)} alt="playback"
                   className="w-full h-full object-contain" />
            ) : scene?.image ? (
              <img src={scene.image} alt="scene" className="w-full h-full object-contain" />
            ) : (
              <div className="text-viper-muted text-sm">Preparing…</div>
            )}
            {editable && (
              <div className="absolute top-3 right-3 text-[10px] px-2 py-1 rounded-md
                              bg-viper-accent/20 text-viper-accent border border-viper-accent/40">
                drag waypoints to edit
              </div>
            )}
          </div>
          {/* Done -> optional video */}
          {(status === "done") && !videoEvt && (
            <div className="text-xs text-viper-muted flex items-center gap-3">
              <span>Animated trajectory playback.</span>
              {scene?.has_blend && (
                <button onClick={onRenderVideo}
                  className="rounded-lg bg-viper-accent hover:bg-indigo-500 text-white px-3 py-1.5 font-medium">
                  🎬 Render full robot-arm video
                </button>
              )}
            </div>
          )}
        </div>

        {/* Conversation + composer */}
        <div className="min-h-0 flex flex-col border-l border-viper-border bg-viper-panel">
          <Feed events={events} awaitingPlan={awaitingPlan} onApprove={onApprove} status={status} />
          <Composer status={status} onSay={onSay} awaiting={!!awaitingPlan} />
        </div>
      </div>
    </div>
  );
}

/* ----------------------------- conversation ----------------------------- */

const STEP_META = {
  session_started: { icon: "🎬", tone: "accent" },
  plan_proposed:   { icon: "🧩", tone: "accent" },
  conflict_detected:{ icon: "⚠", tone: "warn" },
  segment_started: { icon: "→", tone: "muted" },
  path_locked:     { icon: "🔒", tone: "good" },
  refine_iter:     { icon: "✨", tone: "violet" },
  awaiting_input:  { icon: "⏳", tone: "warn" },
  path_committed:  { icon: "✅", tone: "good" },
  render_progress: { icon: "🎞", tone: "muted" },
  session_done:    { icon: "🏁", tone: "accent" },
  session_aborted: { icon: "🛑", tone: "bad" },
  info:            { icon: "·", tone: "muted" },
};
const TONE = {
  accent: "text-viper-accent", warn: "text-viper-warn", good: "text-viper-good",
  bad: "text-viper-bad", muted: "text-viper-muted", violet: "text-violet-300",
};

function Feed({ events, awaitingPlan, onApprove, status }) {
  const endRef = useRef(null);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [events.length]);

  return (
    <div className="flex-1 overflow-y-auto p-3 space-y-2">
      {events.map((e, i) => {
        if (e.type === "user_message")
          return (
            <div key={i} className="flex justify-end animate-slidein">
              <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-viper-accent/90 text-white
                              px-3 py-2 text-sm">{e.message}</div>
            </div>
          );
        // The plan message gets an inline Approve affordance while awaiting.
        const isPlan = e.type === "plan_proposed";
        const m = STEP_META[e.type] || STEP_META.info;
        return (
          <div key={i} className="flex gap-2 animate-slidein">
            <span className="shrink-0 w-6 text-center">{m.icon}</span>
            <div className="min-w-0 flex-1">
              <div className="text-sm text-viper-text/90">{e.message}</div>
              {e.payload?.reason && (
                <div className="text-xs text-viper-muted mt-0.5 italic">“{e.payload.reason}”</div>
              )}
              {isPlan && e.payload?.plan?.length > 0 && (
                <PlanCard plan={e.payload.plan} />
              )}
              {isPlan && awaitingPlan === e.payload && (
                <ApproveRow empty={e.payload.empty} onApprove={onApprove} />
              )}
              {typeof e.payload?.risk === "number" && (
                <span className="text-[10px] text-viper-muted">risk {e.payload.risk.toFixed(2)}
                  {e.payload?.metrics && ` · ${e.payload.metrics.num_waypoints} wp`}</span>
              )}
            </div>
          </div>
        );
      })}
      <div ref={endRef} />
    </div>
  );
}

function PlanCard({ plan }) {
  const OP = { move_to: ["→", "text-sky-300"], pick: ["✊", "text-viper-warn"], place: ["📍", "text-viper-good"] };
  return (
    <div className="mt-1.5 rounded-xl border border-viper-border bg-viper-panel2 p-2 space-y-0.5">
      {plan.map((s) => {
        const [icon, color] = OP[s.op] || ["·", "text-viper-muted"];
        return (
          <div key={s.step} className="flex items-center gap-2 text-xs">
            <span className="w-4 text-viper-muted text-center">{s.step}</span>
            <span className={color}>{icon}</span>
            <span className="text-viper-text/80">{s.op} target {s.args?.target_id}
              {s.args?.destination && <span className="text-viper-muted"> → [{s.args.destination.join(",")}]</span>}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function ApproveRow({ empty, onApprove }) {
  return (
    <div className="mt-2 flex items-center gap-2">
      {!empty && (
        <button onClick={onApprove}
          className="rounded-lg bg-viper-good/20 text-viper-good hover:bg-viper-good/30
                     text-xs font-medium px-3 py-1.5">✓ Approve &amp; run</button>
      )}
      <span className="text-[11px] text-viper-muted">
        {empty ? "Reply below to fix the goal." : "…or type a change below to refine."}
      </span>
    </div>
  );
}

function Composer({ status, onSay, awaiting }) {
  const [text, setText] = useState("");
  const idle = status === "idle";
  const placeholder = awaiting ? "Approve, or tell me what to change…"
    : status === "running" ? "Coach the VLM — e.g. route along the right side…"
    : status === "done" || status === "aborted" ? "Ask a follow-up or start a new instruction…"
    : "Type a message…";
  const send = () => { if (text.trim()) { onSay(text.trim()); setText(""); } };
  return (
    <div className="shrink-0 border-t border-viper-border p-3">
      <div className="flex items-end gap-2 rounded-2xl bg-viper-panel2 border border-viper-border px-3 py-1.5
                      focus-within:ring-1 focus-within:ring-viper-accent">
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={1}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
          placeholder={placeholder} disabled={idle}
          className="flex-1 bg-transparent py-1.5 text-sm resize-none max-h-28
                     placeholder:text-viper-muted/60 focus:outline-none disabled:opacity-50" />
        <button onClick={send} disabled={idle || !text.trim()}
          className="shrink-0 mb-0.5 rounded-lg bg-viper-accent hover:bg-indigo-500 text-white
                     text-sm font-medium px-3 py-1.5 disabled:opacity-40">Send</button>
      </div>
    </div>
  );
}

function Transport({ status, running, onControl }) {
  if (!running) {
    const label = { done: "Done", aborted: "Stopped", idle: "Idle" }[status] || status;
    return <span className="text-xs px-2.5 py-1 rounded-full bg-viper-panel2 text-viper-muted">{label}</span>;
  }
  return (
    <div className="flex gap-2">
      {status === "paused"
        ? <Btn onClick={() => onControl("continue")} cls="bg-viper-good/20 text-viper-good">▶ Resume</Btn>
        : <Btn onClick={() => onControl("pause")} cls="bg-viper-warn/15 text-viper-warn">⏸ Pause</Btn>}
      <Btn onClick={() => onControl("stop")} cls="bg-viper-bad/15 text-viper-bad">⏹ Stop</Btn>
    </div>
  );
}
function Btn({ children, cls = "", ...p }) {
  return <button {...p} className={`rounded-lg px-3 py-1.5 text-xs font-medium ${cls}`}>{children}</button>;
}
