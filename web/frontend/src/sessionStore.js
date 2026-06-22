// Lightweight client-side session history (localStorage).
// Each record: {id, sessionId, goal, scene, vlm, createdAt, status, thumb, events?}

const KEY = "viper.sessions.v1";

export function loadSessions() {
  try {
    return JSON.parse(localStorage.getItem(KEY) || "[]");
  } catch {
    return [];
  }
}

export function saveSession(rec) {
  const all = loadSessions().filter((s) => s.id !== rec.id);
  // Keep events small: cap stored events per session.
  const trimmed = { ...rec, events: (rec.events || []).slice(-200) };
  all.unshift(trimmed);
  localStorage.setItem(KEY, JSON.stringify(all.slice(0, 50)));
}

export function deleteSession(id) {
  const all = loadSessions().filter((s) => s.id !== id);
  localStorage.setItem(KEY, JSON.stringify(all));
}
