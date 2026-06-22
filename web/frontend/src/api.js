// Tiny API client for the Co-Pilot backend.

export async function getScenes() {
  const r = await fetch("/api/scenes");
  return r.json();
}

export async function getModels() {
  const r = await fetch("/api/models");
  return r.json();
}

export async function startSession({ goal, dataset_path, vlm, conflict_default }) {
  const r = await fetch("/api/session/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ goal, dataset_path, vlm, conflict_default }),
  });
  return r.json();
}

// Upload a user scene (image + dataset.json [+ optional .blend]).
// Returns a scene record { id, dataset_path, image, num_objects, is_3d }.
export async function uploadScene({ image, dataset, blend }) {
  const fd = new FormData();
  fd.append("image", image);
  fd.append("dataset", dataset);
  if (blend) fd.append("blend", blend);
  const r = await fetch("/api/upload", { method: "POST", body: fd });
  if (!r.ok) throw new Error((await r.json()).error || "upload failed");
  return r.json();
}

// Resolve a backend image path to a served URL.
export function imageUrl(path) {
  if (!path) return null;
  return `/api/image?path=${encodeURIComponent(path)}`;
}

// Open the live event WebSocket. onEvent(evt); onEnd(status).
export function openSessionSocket(sid, { onEvent, onEnd }) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/${sid}`);
  ws.onmessage = (m) => {
    const data = JSON.parse(m.data);
    if (data.type === "session_end") onEnd?.(data.status);
    else onEvent?.(data);
  };
  return ws;
}

export function sendAction(ws, action, text) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ action, text }));
  }
}

// One chat input -> backend routes by session state (approve / refine / coach).
export async function sendMessage(sid, text) {
  const r = await fetch(`/api/session/${sid}/message`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  return r.json();
}

// Replay: fetch a finished session's recorded events from the backend.
export async function getSessionEvents(sid) {
  const r = await fetch(`/api/session/${sid}/events`);
  if (!r.ok) return null;
  return r.json();
}

// Trigger the optional Blender video render for a finished session.
export async function renderVideo(sid) {
  const r = await fetch(`/api/session/${sid}/render_video`, { method: "POST" });
  return r.json();
}

export function videoUrl(path) {
  return path ? `/api/video?path=${encodeURIComponent(path)}` : null;
}
