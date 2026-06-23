import { useEffect, useMemo, useRef, useState } from "react";
import { imageUrl } from "../api";
import TrajectoryOverlay from "./TrajectoryOverlay";

/**
 * One session = one conversation.
 *
 * Layout: a big visual STAGE on the left (scene / live trajectory overlay / GIF /
 * video) with a slim live STATUS STRIP underneath it; a clean CHAT column on the
 * right. The chat carries only conversation — your messages, the planner's
 * reason, the plan card, approve/refine. All of the solver's internal chatter
 * (explore/refine/lock/commit) and render progress live in the status strip, so
 * the conversation stays calm and readable.
 */
export default function Session({ events, status, scene, goal, onSay, onApprove,
                                  onControl, onEdit, onRenderVideo, onCancelRender }) {
  const geoEvt = useMemo(
    () => [...events].reverse().find((e) => e.payload?.geometry), [events]);
  const gifEvt = useMemo(
    () => [...events].reverse().find(
      (e) => e.type === "session_done" && e.image_path?.endsWith(".gif")), [events]);
  const videoEvt = useMemo(
    () => [...events].reverse().find((e) => e.payload?.video), [events]);

  const running = status === "running" || status === "paused";
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

      <div className="flex-1 min-h-0 grid grid-cols-[1fr_400px]">
        {/* Stage + status strip */}
        <div className="min-h-0 flex flex-col p-6 gap-3">
          <div className={`relative flex-1 min-h-0 w-full rounded-2xl overflow-hidden border bg-black
                          shadow-2xl shadow-black/50 grid place-items-center transition
                          ${editable ? "border-viper-accent ring-1 ring-viper-accent/40" : "border-viper-border"}`}>
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

          <StatusStrip events={events} status={status} hasVideo={!!videoEvt}
                       hasBlend={scene?.has_blend}
                       onRender={onRenderVideo} onCancel={onCancelRender} />
        </div>

        {/* Conversation + composer */}
        <div className="min-h-0 flex flex-col border-l border-viper-border bg-viper-panel">
          <Feed events={events} awaitingPlan={awaitingPlan} onApprove={onApprove} />
          <Composer status={status} onSay={onSay} awaiting={!!awaitingPlan} />
        </div>
      </div>
    </div>
  );
}

/* ----------------------- live status strip (under stage) ----------------------- */

function fmt(s) {
  if (s == null) return "—";
  s = Math.round(s);
  const m = Math.floor(s / 60), sec = s % 60;
  return m > 0 ? `${m}m ${sec}s` : `${sec}s`;
}

// Derive the single current activity line from the event stream.
function deriveActivity(events) {
  // Render takes precedence while it's live.
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (e.payload?.video) break;                      // render finished
    if (e.type === "render_progress")
      return { kind: "render", ...e.payload };
    if (e.type === "info" && e.payload?.cancelled)
      return { kind: "cancelled" };
  }
  // Otherwise reflect the latest solver step.
  const order = ["session_done", "session_aborted", "path_committed",
                 "refine_iter", "path_locked", "awaiting_input", "segment_started"];
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (order.includes(e.type)) return { kind: "solve", evt: e };
    if (e.type === "plan_proposed") return { kind: "planned", evt: e };
  }
  return null;
}

function StatusStrip({ events, status, hasVideo, hasBlend, onRender, onCancel }) {
  const act = useMemo(() => deriveActivity(events), [events]);

  // Done with no video yet -> offer the render (or show its progress).
  if (status === "done" && !hasVideo) {
    if (act?.kind === "render" && act.total) {
      return (
        <div className="shrink-0 rounded-xl border border-viper-border bg-viper-panel2/60 px-4 py-2.5">
          <div className="flex items-center justify-between text-xs mb-1.5">
            <span className="text-viper-text/90">🎞 Rendering robot-arm video
              <span className="text-viper-muted"> · {act.done}/{act.total} ({act.pct}%)</span>
            </span>
            <button onClick={onCancel} className="text-viper-bad hover:text-red-300 font-medium">
              ■ Interrupt
            </button>
          </div>
          <Bar pct={act.pct} />
          <div className="text-[10px] text-viper-muted mt-1">
            elapsed {fmt(act.elapsed_s)} · ETA {fmt(act.eta_s)}
          </div>
        </div>
      );
    }
    return (
      <div className="shrink-0 flex items-center justify-between rounded-xl border border-viper-border
                      bg-viper-panel2/60 px-4 py-2.5">
        <span className="text-xs text-viper-muted flex items-center gap-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-viper-accent" /> Plan complete
          {act?.kind === "cancelled" && <span className="text-viper-bad ml-2">render cancelled</span>}
        </span>
        {hasBlend && (
          <button onClick={() => onRender()}
            className="rounded-lg bg-viper-accent hover:bg-indigo-500 text-white px-3 py-1.5 text-xs font-medium">
            🎬 Render robot-arm video
          </button>
        )}
      </div>
    );
  }

  if (!act) {
    return (
      <div className="shrink-0 rounded-xl border border-viper-border bg-viper-panel2/40 px-4 py-2.5
                      text-xs text-viper-muted">
        Ready — describe a goal or approve a plan to begin.
      </div>
    );
  }

  if (act.kind === "render" && act.total) {
    return (
      <div className="shrink-0 rounded-xl border border-viper-border bg-viper-panel2/60 px-4 py-2.5">
        <div className="flex items-center justify-between text-xs mb-1.5">
          <span className="text-viper-text/90">🎞 Rendering video
            <span className="text-viper-muted"> · {act.done}/{act.total} ({act.pct}%)</span>
          </span>
          <button onClick={onCancel} className="text-viper-bad hover:text-red-300 font-medium">■ Interrupt</button>
        </div>
        <Bar pct={act.pct} />
        <div className="text-[10px] text-viper-muted mt-1">
          elapsed {fmt(act.elapsed_s)} · ETA {fmt(act.eta_s)}
        </div>
      </div>
    );
  }

  // Solving: one calm line describing the current step + a thin scene-progress bar.
  const line = solveLine(act, events);
  const prog = stepProgress(events);
  return (
    <div className="shrink-0 rounded-xl border border-viper-border bg-viper-panel2/60 px-4 py-2.5">
      <div className="flex items-center gap-2 text-xs">
        <span className={`w-2 h-2 rounded-full ${line.pulse ? "bg-viper-good animate-pulse" : "bg-viper-muted"}`} />
        <span className="text-viper-text/90">{line.text}</span>
        {line.risk != null && (
          <span className="text-[10px] text-viper-muted">· risk {line.risk.toFixed(2)}</span>
        )}
        {prog && <span className="ml-auto text-[10px] text-viper-muted">{prog.done}/{prog.total} sub-tasks</span>}
      </div>
      {prog && <div className="mt-1.5"><Bar pct={Math.round((prog.done / prog.total) * 100)} /></div>}
    </div>
  );
}

function Bar({ pct }) {
  return (
    <div className="h-1.5 rounded-full bg-black/40 overflow-hidden">
      <div className="h-full bg-viper-accent transition-all duration-500" style={{ width: `${pct}%` }} />
    </div>
  );
}

// Map the latest solver event to a human one-liner.
function solveLine(act, events) {
  const e = act.evt;
  const step = e?.step ?? e?.payload?.step;
  const risk = typeof e?.payload?.risk === "number" ? e.payload.risk : null;
  switch (e?.type) {
    case "session_done":   return { text: "Done — plan executed", pulse: false };
    case "session_aborted":return { text: "Stopped", pulse: false };
    case "segment_started":return { text: `Solving sub-task ${step ?? ""}…`, pulse: true };
    case "awaiting_input": return { text: `Reviewing candidate for step ${step ?? ""}`, pulse: true, risk };
    case "path_locked":    return { text: `Feasible path locked (step ${step ?? ""})`, pulse: true, risk };
    case "refine_iter":    return { text: `Refining step ${step ?? ""}`, pulse: true, risk };
    case "path_committed": return { text: `Committed step ${step ?? ""}`, pulse: true, risk };
    case "plan_proposed":  return { text: "Plan ready for review", pulse: false };
    default:               return { text: "Working…", pulse: true };
  }
}

// How many sub-tasks committed vs. planned (drives the strip's progress bar).
function stepProgress(events) {
  const plan = [...events].reverse().find((e) => e.type === "plan_proposed");
  const total = plan?.payload?.plan?.length;
  if (!total) return null;
  const after = events.slice(events.indexOf(plan) + 1);
  const done = after.filter((e) => e.type === "path_committed"
    || (e.type === "segment_started" && ["pick", "place"].includes(e.payload?.op))).length;
  return { done: Math.min(done, total), total };
}

/* ------------------------------- conversation ------------------------------- */

function Feed({ events, awaitingPlan, onApprove }) {
  const endRef = useRef(null);
  // Only conversational events reach the chat. Solver/render noise is in the strip.
  const msgs = useMemo(() => events.filter((e) =>
    ["user_message", "plan_proposed", "session_aborted"].includes(e.type)
    || (e.type === "info" && e.message && !e.payload?.cancelled)
  ), [events]);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [msgs.length]);

  return (
    <div className="flex-1 overflow-y-auto p-4 space-y-3">
      {msgs.length === 0 && (
        <div className="text-xs text-viper-muted/70 mt-2">
          The planner's reasoning and the proposed plan will appear here. Approve it,
          or reply to refine.
        </div>
      )}
      {msgs.map((e, i) => {
        if (e.type === "user_message")
          return (
            <div key={i} className="flex justify-end animate-slidein">
              <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-viper-accent/90 text-white
                              px-3.5 py-2 text-sm leading-relaxed">{e.message}</div>
            </div>
          );

        if (e.type === "plan_proposed")
          return (
            <PlanMessage key={i} payload={e.payload}
                         awaiting={awaitingPlan === e.payload} onApprove={onApprove} />
          );

        // info / aborted -> a quiet system line
        return (
          <div key={i} className="flex justify-center animate-slidein">
            <div className="text-[11px] text-viper-muted bg-viper-panel2/50 rounded-full px-3 py-1">
              {e.message}
            </div>
          </div>
        );
      })}
      <div ref={endRef} />
    </div>
  );
}

function PlanMessage({ payload, awaiting, onApprove }) {
  const conflicts = payload?.conflicts || [];
  return (
    <div className="animate-slidein">
      <div className="flex items-center gap-2 mb-1.5">
        <span className="w-6 h-6 rounded-lg bg-viper-accent/15 grid place-items-center text-sm">🧩</span>
        <span className="text-xs font-medium text-viper-text/90">
          {payload?.plan?.length ? `Plan · ${payload.plan.length} steps` : "No plan produced"}
        </span>
      </div>
      {payload?.reason && (
        <p className="text-xs text-viper-muted italic leading-relaxed mb-2 pl-8">"{payload.reason}"</p>
      )}
      {payload?.plan?.length > 0 && <PlanCard plan={payload.plan} />}
      {conflicts.length > 0 && (
        <div className="mt-2 rounded-xl border border-viper-warn/30 bg-viper-warn/5 p-2.5 space-y-1">
          <div className="text-[10px] uppercase tracking-wider text-viper-warn/90 flex items-center gap-1">
            ⚠ {conflicts.length} conflict{conflicts.length > 1 ? "s" : ""} auto-resolved
          </div>
          {conflicts.map((c, i) => (
            <div key={i} className="text-[11px] text-viper-text/70 leading-snug">• {c}</div>
          ))}
        </div>
      )}
      {awaiting && <ApproveRow empty={payload?.empty} onApprove={onApprove} />}
    </div>
  );
}

const OPLABEL = { move_to: "move to", pick: "pick", place: "place" };

function PlanCard({ plan }) {
  const OP = { move_to: ["→", "text-sky-300"], pick: ["✊", "text-viper-warn"], place: ["📍", "text-viper-good"] };
  return (
    <div className="rounded-xl border border-viper-border bg-viper-panel2 p-2.5 space-y-0.5">
      {plan.map((s) => {
        const [icon, color] = OP[s.op] || ["·", "text-viper-muted"];
        return (
          <div key={s.step} className="flex items-center gap-2 text-xs">
            <span className="w-4 text-viper-muted text-center">{s.step}</span>
            <span className={color}>{icon}</span>
            <span className="text-viper-text/80">{OPLABEL[s.op] || s.op}{" "}
              <span className="text-viper-text/95">{s.label || `object ${s.args?.target_id}`}</span>
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
    <div className="mt-2.5 flex items-center gap-2 pl-8">
      {!empty && (
        <button onClick={onApprove}
          className="rounded-lg bg-viper-good/20 text-viper-good hover:bg-viper-good/30
                     text-xs font-medium px-3.5 py-1.5">✓ Approve &amp; run</button>
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
