import { useMemo } from "react";
import { imageUrl } from "../api";
import TrajectoryOverlay from "./TrajectoryOverlay";
import PlanPanel from "./PlanPanel";

const PHASE_META = {
  session_started:   { icon: "🎬", label: "Scene",     tone: "accent" },
  plan_proposed:     { icon: "🧩", label: "Plan",      tone: "accent" },
  conflict_detected: { icon: "⚠", label: "Conflict",  tone: "warn" },
  segment_started:   { icon: "→", label: "Sub-task",  tone: "muted" },
  explore_iter:      { icon: "🔍", label: "Explore",   tone: "sky" },
  path_locked:       { icon: "🔒", label: "Locked",    tone: "good" },
  refine_iter:       { icon: "✨", label: "Refine",    tone: "violet" },
  awaiting_input:    { icon: "⏳", label: "Review",    tone: "warn" },
  path_committed:    { icon: "✅", label: "Committed", tone: "good" },
  ik_done:           { icon: "🦾", label: "IK",        tone: "muted" },
  render_progress:   { icon: "🎞", label: "Render",    tone: "muted" },
  session_done:      { icon: "🏁", label: "Done",      tone: "accent" },
  session_aborted:   { icon: "🛑", label: "Aborted",   tone: "bad" },
  info:              { icon: "·", label: "Info",       tone: "muted" },
};
const TONE = {
  accent: "text-viper-accent", warn: "text-viper-warn", good: "text-viper-good",
  bad: "text-viper-bad", muted: "text-viper-muted", sky: "text-sky-300", violet: "text-violet-300",
};
const DOT = {
  accent: "bg-viper-accent", warn: "bg-viper-warn", good: "bg-viper-good",
  bad: "bg-viper-bad", muted: "bg-viper-muted", sky: "bg-sky-400", violet: "bg-violet-400",
};

export default function Stage({ events, status, cursor, setCursor, scene, goal, onEdit, running, onAction }) {
  // Plan-approval gate: the latest event is a plan_approval awaiting_input.
  const lastEvt = events[events.length - 1];
  const planGate = running && lastEvt?.type === "awaiting_input"
                   && lastEvt?.payload?.kind === "plan_approval" ? lastEvt.payload : null;
  // Frames = steps that carry either structured geometry (drawn as an SVG
  // overlay) OR a rendered image (committed/final). Geometry is preferred.
  const frames = useMemo(
    () => events.filter((e) => e.payload?.geometry || e.image_path), [events]);
  const idx = cursor < 0 ? frames.length - 1 : Math.min(cursor, frames.length - 1);
  const cur = frames[idx];
  const geo = cur?.payload?.geometry;
  const src = cur?.image_path ? imageUrl(cur.image_path) : scene?.image;
  const m = cur?.payload?.metrics;
  const meta = cur ? (PHASE_META[cur.type] || PHASE_META.info) : null;
  // The latest awaiting_input frame is editable while the session is live.
  const isLatest = idx === frames.length - 1;
  const editable = running && geo && cur?.type === "awaiting_input" && isLatest;

  // latest non-image event (for the live status line under the canvas)
  const last = events[events.length - 1];

  // Video: a SESSION_DONE event may carry an mp4 path (after a render).
  const videoEvt = useMemo(
    () => [...events].reverse().find((e) => e.payload?.video), [events]);
  const done = status === "done" || status === "aborted";

  return (
    <div className="relative flex-1 min-h-0 flex flex-col">
      {planGate && (
        <PlanPanel plan={planGate.plan} numConflicts={planGate.num_conflicts}
          onApprove={() => onAction("approve")}
          onRefine={(hint) => onAction("correction", hint)}
          onCancel={() => onAction("stop")} />
      )}
      {/* Goal header */}
      <div className="h-14 shrink-0 flex items-center justify-between px-6 border-b border-viper-border">
        <div className="min-w-0">
          <div className="text-[10px] uppercase tracking-wider text-viper-muted">Goal</div>
          <div className="text-sm text-viper-text/90 truncate">{goal}</div>
        </div>
        <LiveBadge status={status} />
      </div>

      {/* Big evolving canvas */}
      <div className="flex-1 min-h-0 grid place-items-center p-6">
        <div className="relative w-full max-w-4xl">
          <div className={`relative rounded-2xl overflow-hidden border bg-black
                          shadow-2xl shadow-black/50 grid place-items-center transition
                          ${editable ? "border-viper-accent ring-1 ring-viper-accent/50" : "border-viper-border"}`}
               style={{ aspectRatio: "16/9" }}>
            {geo ? (
              <TrajectoryOverlay sceneUrl={geo.scene_image ? imageUrl(geo.scene_image) : scene?.image}
                                 geometry={geo} editable={editable} onEdit={onEdit} />
            ) : src ? (
              <img key={src} src={src} alt="stage"
                   className="w-full h-full object-contain animate-slidein" />
            ) : (
              <Empty />
            )}
            {editable && (
              <div className="absolute top-3 right-3 text-[10px] px-2 py-1 rounded-md
                              bg-viper-accent/20 text-viper-accent border border-viper-accent/40">
                drag waypoints to edit
              </div>
            )}
          </div>

          {/* Floating phase + metric badges */}
          {meta && (
            <div className="absolute top-3 left-3 flex items-center gap-2 px-3 py-1.5 rounded-xl
                            bg-viper-bg/80 backdrop-blur border border-viper-border">
              <span>{meta.icon}</span>
              <span className={`text-xs font-semibold uppercase tracking-wide ${TONE[meta.tone]}`}>{meta.label}</span>
              {typeof cur?.payload?.risk === "number" && <RiskPill risk={cur.payload.risk} />}
            </div>
          )}
          {m && (
            <div className="absolute top-3 right-3 flex items-center gap-3 px-3 py-1.5 rounded-xl
                            bg-viper-bg/80 backdrop-blur border border-viper-border text-xs">
              <span><b>{m.num_waypoints}</b><span className="text-viper-muted"> wp</span></span>
              <span><b>{Math.round(m.length_px)}</b><span className="text-viper-muted"> px</span></span>
            </div>
          )}
        </div>
      </div>

      {/* Live status line */}
      <div className="px-6 pb-2 text-center">
        <div className="text-sm text-viper-text/80 inline-flex items-center gap-2">
          {status === "running" && <span className="w-2 h-2 rounded-full bg-viper-good animate-pulseDot" />}
          {last?.message || "Waiting…"}
        </div>
      </div>

      {/* Step rail (scrubbable) */}
      <div className="shrink-0 border-t border-viper-border px-6 py-3 overflow-x-auto">
        <div className="flex items-center gap-1 min-w-min">
          {frames.map((e, i) => {
            const mt = PHASE_META[e.type] || PHASE_META.info;
            const on = i === idx;
            return (
              <button key={i} onClick={() => setCursor(i)} title={e.message}
                className={`group relative shrink-0 flex flex-col items-center gap-1 px-1.5 py-1 rounded-lg
                  transition ${on ? "bg-viper-panel2" : "hover:bg-viper-panel"}`}>
                <span className={`w-2.5 h-2.5 rounded-full ${DOT[mt.tone]} ${on ? "ring-2 ring-offset-2 ring-offset-viper-bg ring-white/40" : ""}`} />
                <span className={`text-[9px] ${on ? TONE[mt.tone] : "text-viper-muted"}`}>{mt.label}</span>
              </button>
            );
          })}
          {status === "running" && (
            <div className="shrink-0 flex flex-col items-center gap-1 px-1.5 py-1">
              <span className="w-2.5 h-2.5 rounded-full bg-viper-muted animate-pulseDot" />
              <span className="text-[9px] text-viper-muted">…</span>
            </div>
          )}
          {frames.length > 0 && (
            <button onClick={() => setCursor(-1)}
              className="shrink-0 ml-2 text-[10px] text-viper-muted hover:text-viper-text px-2 py-1 rounded">
              live ⟶
            </button>
          )}
        </div>
      </div>

      {/* Done footer: optional video render */}
      {done && (
        <VideoFooter done={done} hasBlend={!!scene?.has_blend} videoEvt={videoEvt}
                     onRender={() => onAction("render_video")} />
      )}
    </div>
  );
}

function VideoFooter({ hasBlend, videoEvt, onRender }) {
  const video = videoEvt?.payload?.video;
  return (
    <div className="shrink-0 border-t border-viper-border px-6 py-3 flex items-center gap-4">
      {video ? (
        <video controls src={`/api/video?path=${encodeURIComponent(video)}`}
               className="h-40 rounded-lg border border-viper-border bg-black" />
      ) : hasBlend ? (
        <>
          <button onClick={onRender}
            className="rounded-xl bg-viper-accent hover:bg-indigo-500 text-white text-sm font-medium px-4 py-2.5">
            🎬 Generate robot-arm video
          </button>
          <span className="text-xs text-viper-muted">
            Renders the committed plan in Blender (optional). Takes a few minutes.
          </span>
        </>
      ) : (
        <span className="text-xs text-viper-muted">
          No <code>.blend</code> uploaded → simulation video unavailable. The trajectory overlay above is your playback.
        </span>
      )}
    </div>
  );
}

function Empty() {
  return (
    <div className="text-center text-viper-muted">
      <div className="text-5xl mb-2 opacity-30">⟜</div>
      <div className="text-sm">Preparing the scene…</div>
    </div>
  );
}

function LiveBadge({ status }) {
  const map = {
    running: ["bg-viper-good/15 text-viper-good", "Planning"],
    paused:  ["bg-viper-warn/15 text-viper-warn", "Paused"],
    done:    ["bg-viper-accent/15 text-viper-accent", "Done"],
    aborted: ["bg-viper-bad/15 text-viper-bad", "Stopped"],
    error:   ["bg-viper-bad/15 text-viper-bad", "Error"],
    idle:    ["bg-viper-panel2 text-viper-muted", "Idle"],
  };
  const [cls, label] = map[status] || map.idle;
  return <span className={`text-xs px-2.5 py-1 rounded-full ${cls}`}>{label}</span>;
}

function RiskPill({ risk }) {
  const c = risk < 0.2 ? "bg-viper-good/15 text-viper-good"
          : risk < 0.5 ? "bg-viper-warn/15 text-viper-warn"
          : "bg-viper-bad/15 text-viper-bad";
  return <span className={`text-[10px] px-1.5 py-0.5 rounded ${c}`}>risk {risk.toFixed(2)}</span>;
}
