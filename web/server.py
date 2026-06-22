"""
Deep VIPER Co-Pilot — FastAPI backend.

Wraps the existing harness so a browser UI can:
  - list scenes and start a planning session
  - stream live events over WebSocket as the harness runs
  - send control actions back (pause / resume / stop / correction)
  - fetch any image the harness produced (scene renders, iteration frames)

Run:  python -m web.server   (or: uvicorn web.server:app --reload)
The harness runs in a background thread per session; events flow through a
thread-safe SessionHandle (see deep_viper/session/bridge.py).
"""
from __future__ import annotations

import asyncio
import json
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from deep_viper.config import load_config
from deep_viper.planning.harness import run_session
from deep_viper.session.bridge import SessionHandle, QueueController
from deep_viper.session.events import EventType

REPO = Path(__file__).resolve().parent.parent
SCENES_DIR = REPO / "data" / "blender" / "scenes"
RUNS_DIR = REPO / "runs"
FRONTEND_DIST = REPO / "web" / "frontend" / "dist"

app = FastAPI(title="Deep VIPER Co-Pilot")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])

# Active sessions by id
SESSIONS: dict[str, SessionHandle] = {}


# --------------------------------------------------------------------------- #
# Scene + image endpoints
# --------------------------------------------------------------------------- #
@app.get("/api/scenes")
def list_scenes():
    scenes = []
    if SCENES_DIR.exists():
        for d in sorted(SCENES_DIR.iterdir()):
            ds = d / "dataset.json"
            if ds.exists():
                data = json.loads(ds.read_text())
                scenes.append({
                    "id": d.name,
                    "dataset_path": str(ds),
                    "image": f"/api/image?path={d / 'render.png'}",
                    "num_objects": len(data.get("objects", [])),
                    "objects": [{"id": o["id"], "label": o["label"],
                                 "center": o["center"]} for o in data.get("objects", [])],
                    "sample_goals": data.get("sample_goals", []),
                    "is_3d": "camera" in data,
                })
    # also expose the 2D photo dataset
    twod = REPO / "data" / "dataset_2d-6.json"
    if twod.exists():
        d = json.loads(twod.read_text())
        scenes.insert(0, {
            "id": "2d-6 (photo)", "dataset_path": str(twod),
            "image": f"/api/image?path={REPO / 'data' / '2d-6.png'}",
            "num_objects": len(d.get("objects", [])),
            "objects": [{"id": o["id"], "label": o["label"], "center": o["center"]}
                        for o in d.get("objects", [])],
            "sample_goals": [], "is_3d": False,
        })
    return scenes


@app.get("/api/image")
def get_image(path: str):
    p = Path(path)
    # Only serve images from within the repo (safety).
    try:
        p.resolve().relative_to(REPO)
    except ValueError:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    if not p.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(p)


@app.get("/api/models")
def list_models():
    """VLM profiles available in config.yaml."""
    import yaml
    raw = yaml.safe_load((REPO / "config.yaml").read_text())
    return list(raw.get("vlm_profiles", {}).keys()) or ["openai"]


# --------------------------------------------------------------------------- #
# Session lifecycle
# --------------------------------------------------------------------------- #
@app.post("/api/session/start")
async def start_session(body: dict):
    goal = (body.get("goal") or "").strip()
    dataset_path = body.get("dataset_path")
    vlm = body.get("vlm")  # profile name or None
    conflict_default = body.get("conflict_default", "p")
    if not goal or not dataset_path:
        return JSONResponse({"error": "goal and dataset_path required"}, status_code=400)

    sid = uuid.uuid4().hex[:8]
    handle = SessionHandle(session_id=sid)
    SESSIONS[sid] = handle
    cfg = load_config(vlm_profile=vlm)

    def _run():
        handle.status = "running"
        ctl = QueueController(handle)
        try:
            run_session(goal, dataset_path, cfg,
                        conflict_default=conflict_default, controller=ctl)
            if handle.status not in ("aborted",):
                handle.status = "done"
        except SystemExit:
            handle.status = "aborted"
        except Exception as e:
            handle.status = "error"
            handle.events.put(_err_event(str(e)))
        finally:
            handle.events.put(_sentinel_event())

    t = threading.Thread(target=_run, daemon=True)
    handle.thread = t
    t.start()
    return {"session_id": sid, "status": "running"}


@app.post("/api/session/{sid}/action")
async def session_action(sid: str, body: dict):
    h = SESSIONS.get(sid)
    if not h:
        return JSONResponse({"error": "unknown session"}, status_code=404)
    h.submit_action(body.get("action", "continue"), body.get("text"))
    return {"ok": True, "status": h.status}


@app.websocket("/ws/{sid}")
async def session_ws(ws: WebSocket, sid: str):
    await ws.accept()
    h = SESSIONS.get(sid)
    if not h:
        await ws.send_json({"type": "error", "message": "unknown session"})
        await ws.close()
        return

    # Pump events from the harness thread to the socket. Also accept incoming
    # control messages from the client.
    async def pump_events():
        loop = asyncio.get_event_loop()
        while True:
            ev = await loop.run_in_executor(None, h.events.get)  # blocking get
            if ev.type == EventType.INFO and ev.payload.get("_sentinel"):
                await ws.send_json({"type": "session_end", "status": h.status})
                break
            await ws.send_json(ev.to_dict())

    async def pump_control():
        try:
            while True:
                msg = await ws.receive_json()
                h.submit_action(msg.get("action", "continue"), msg.get("text"))
        except WebSocketDisconnect:
            pass

    try:
        await asyncio.gather(pump_events(), pump_control())
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        try:
            await ws.close()
        except Exception:
            pass


def _err_event(msg: str):
    from deep_viper.session.events import Event
    return Event(EventType.SESSION_ABORTED, f"Error: {msg}", {"error": msg})


def _sentinel_event():
    from deep_viper.session.events import Event
    return Event(EventType.INFO, "", {"_sentinel": True})


# Serve the built frontend if present (production); otherwise dev runs Vite.
if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("web.server:app", host="127.0.0.1", port=8000, reload=False)
