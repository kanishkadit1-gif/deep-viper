import { useRef, useState, useEffect } from "react";

/**
 * Draws the scene image with an SVG overlay of obstacles, arm, goal, and the
 * current trajectory (arrows + draggable waypoint handles). Coordinates in the
 * geometry payload are in dataset/image pixels; we map them to the rendered
 * <img> box (object-contain) so the overlay stays aligned at any size.
 *
 * onEdit(newWaypoints) fires when the user finishes dragging a handle.
 */
export default function TrajectoryOverlay({ sceneUrl, geometry, editable, onEdit }) {
  const wrapRef = useRef(null);
  const imgRef = useRef(null);
  const [box, setBox] = useState(null); // {ox,oy,w,h} rendered image rect within wrapper
  const [drag, setDrag] = useState(null); // index being dragged
  const [local, setLocal] = useState(null); // local copy of waypoints while editing

  const W = geometry?.image_size?.width || 1280;
  const H = geometry?.image_size?.height || 720;

  // Compute the object-contain rect of the image inside its wrapper.
  function recompute() {
    const wrap = wrapRef.current;
    if (!wrap) return;
    const cw = wrap.clientWidth, ch = wrap.clientHeight;
    const scale = Math.min(cw / W, ch / H);
    const w = W * scale, h = H * scale;
    setBox({ ox: (cw - w) / 2, oy: (ch - h) / 2, w, h, scale });
  }
  useEffect(() => {
    recompute();
    const ro = new ResizeObserver(recompute);
    if (wrapRef.current) ro.observe(wrapRef.current);
    return () => ro.disconnect();
  }, [W, H, sceneUrl]);

  // Reset local edit copy when geometry changes.
  useEffect(() => {
    setLocal(null); setDrag(null);
  }, [geometry]);

  const waypoints = local || geometry?.candidate || geometry?.best || [];
  const arm = geometry?.arm_pos;
  const goal = geometry?.goal_pos;
  const arrowScores = geometry?.arrow_scores || [];

  // px (image space) -> screen (wrapper space)
  const toScreen = (p) => box ? [box.ox + p[0] * box.scale, box.oy + p[1] * box.scale] : [0, 0];
  // screen -> image space
  const toImage = (sx, sy) => box ? [Math.round((sx - box.ox) / box.scale), Math.round((sy - box.oy) / box.scale)] : [0, 0];

  function onPointerDown(i) { if (editable) setDrag(i); }
  function onPointerMove(e) {
    if (drag == null || !box) return;
    const r = wrapRef.current.getBoundingClientRect();
    const pt = toImage(e.clientX - r.left, e.clientY - r.top);
    const wp = [...(local || waypoints)];
    wp[drag] = [Math.max(0, Math.min(W, pt[0])), Math.max(0, Math.min(H, pt[1]))];
    setLocal(wp);
  }
  function onPointerUp() {
    if (drag != null && local) onEdit?.(local);
    setDrag(null);
  }

  const riskColor = (r) =>
    r == null ? "#5b8cff" : r < 0.2 ? "#34d399" : r < 0.5 ? "#fbbf24" : "#f87171";

  // full point list = arm + waypoints (arrows connect consecutive points)
  const pts = arm ? [arm, ...waypoints] : waypoints;

  return (
    <div ref={wrapRef} className="relative w-full h-full"
         onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerLeave={onPointerUp}>
      <img ref={imgRef} src={sceneUrl} alt="scene" onLoad={recompute}
           className="absolute inset-0 w-full h-full object-contain select-none pointer-events-none" />
      {box && geometry && (
        <svg className="absolute inset-0 w-full h-full" style={{ touchAction: "none" }}>
          <defs>
            <marker id="arrow" markerWidth="8" markerHeight="8" refX="6" refY="3"
                    orient="auto"><path d="M0,0 L6,3 L0,6 Z" fill="#e6e9f0" /></marker>
          </defs>

          {/* faded non-best candidate trajectories */}
          {(geometry.trajectories || []).map((t, ti) => {
            if (t === waypoints) return null;
            const tp = arm ? [arm, ...t] : t;
            return <polyline key={"c" + ti} fill="none" stroke="#5b8cff" strokeOpacity="0.15"
              strokeWidth="2" points={tp.map(toScreen).map((p) => p.join(",")).join(" ")} />;
          })}

          {/* obstacles */}
          {(geometry.obstacles || []).map((o) => {
            const [x1, y1, x2, y2] = o.bbox;
            const [sx, sy] = toScreen([x1, y1]);
            const [ex, ey] = toScreen([x2, y2]);
            return (
              <g key={o.id}>
                <rect x={sx} y={sy} width={ex - sx} height={ey - sy}
                      fill="#f87171" fillOpacity="0.07" stroke="#f87171" strokeOpacity="0.5"
                      strokeWidth="1.5" rx="3" />
                <text x={sx + 3} y={sy - 4} fill="#f87171" fontSize="10" opacity="0.8">{o.label}</text>
              </g>
            );
          })}

          {/* current trajectory arrows */}
          {pts.slice(0, -1).map((p, i) => {
            const [x1, y1] = toScreen(p);
            const [x2, y2] = toScreen(pts[i + 1]);
            const risk = arrowScores[i]?.risk;
            return <line key={"a" + i} x1={x1} y1={y1} x2={x2} y2={y2}
              stroke={riskColor(risk)} strokeWidth="2.5" markerEnd="url(#arrow)" opacity="0.95" />;
          })}

          {/* arm start */}
          {arm && (() => { const [x, y] = toScreen(arm);
            return <g><circle cx={x} cy={y} r="7" fill="#5b8cff" />
              <text x={x + 10} y={y + 4} fill="#5b8cff" fontSize="10">arm</text></g>; })()}

          {/* goal */}
          {goal && (() => { const [x, y] = toScreen(goal);
            return <g><circle cx={x} cy={y} r="6" fill="none" stroke="#34d399" strokeWidth="2.5" />
              <line x1={x-9} y1={y} x2={x+9} y2={y} stroke="#34d399" strokeWidth="1.5"/>
              <line x1={x} y1={y-9} x2={x} y2={y+9} stroke="#34d399" strokeWidth="1.5"/></g>; })()}

          {/* draggable waypoint handles */}
          {waypoints.map((p, i) => {
            const [x, y] = toScreen(p);
            return (
              <circle key={"w" + i} cx={x} cy={y} r={editable ? 8 : 5}
                fill="#11151f" stroke="#e6e9f0" strokeWidth="2"
                style={{ cursor: editable ? "grab" : "default" }}
                onPointerDown={() => onPointerDown(i)} />
            );
          })}
        </svg>
      )}

      {editable && local && (
        <div className="absolute bottom-3 left-1/2 -translate-x-1/2 flex gap-2">
          <button onClick={() => onEdit?.(local)}
            className="rounded-lg bg-viper-accent hover:bg-indigo-500 text-white text-xs font-medium px-3 py-1.5">
            ✓ Apply edit & re-score
          </button>
          <button onClick={() => setLocal(null)}
            className="rounded-lg bg-viper-panel2 border border-viper-border text-xs px-3 py-1.5">
            Reset
          </button>
        </div>
      )}
    </div>
  );
}
