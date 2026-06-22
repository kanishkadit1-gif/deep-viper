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
