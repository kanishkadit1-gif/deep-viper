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

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File
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
UPLOADS_DIR = REPO / "data" / "uploads"
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


@app.post("/api/upload")
async def upload_scene(image: UploadFile = File(...),
                       dataset: UploadFile = File(...),
                       blend: UploadFile | None = File(None)):
    """
    Accept a user scene: image + dataset.json (+ optional .blend). Saves them to
    data/uploads/<id>/, rewrites dataset image_path to the saved image, and
    returns a scene record the session can run on.
    """
    import uuid as _uuid
    uid = _uuid.uuid4().hex[:8]
    d = UPLOADS_DIR / uid
    d.mkdir(parents=True, exist_ok=True)

    img_path = d / ("render" + Path(image.filename or "img.png").suffix.lower())
    img_path.write_bytes(await image.read())

    try:
        data = json.loads((await dataset.read()).decode("utf-8"))
    except Exception as e:
        return JSONResponse({"error": f"dataset.json invalid: {e}"}, status_code=400)
    if "objects" not in data:
        return JSONResponse({"error": "dataset.json must contain 'objects'"}, status_code=400)

    # Point the dataset at the uploaded image; default image_size if absent.
    data["image_path"] = str(img_path)
    data.setdefault("image_size", {"width": 1280, "height": 720})

    has_blend = False
    if blend is not None and blend.filename:
        blend_path = d / "scene.blend"
        blend_path.write_bytes(await blend.read())
        data["blend_path"] = str(blend_path)
        has_blend = True

    ds_path = d / "dataset.json"
    ds_path.write_text(json.dumps(data, indent=2))

    return {
        "id": f"upload/{uid}",
        "dataset_path": str(ds_path),
        "image": f"/api/image?path={img_path}",
        "num_objects": len(data.get("objects", [])),
        "is_3d": "camera" in data,
        "has_blend": has_blend,
    }


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
    handle.goal = goal
    handle.dataset_path = dataset_path
    # Record a .blend path if the dataset references one (uploads set this).
    try:
        ds = json.loads(Path(dataset_path).read_text())
        handle.blend_path = ds.get("blend_path", "")
    except Exception:
        pass
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
            _persist_session(sid, handle, goal, dataset_path, vlm)
            handle.events.put(_sentinel_event())

    t = threading.Thread(target=_run, daemon=True)
    handle.thread = t
    t.start()
    return {"session_id": sid, "status": "running"}


SESSIONS_STORE = RUNS_DIR / "_sessions"


def _persist_session(sid, handle, goal, dataset_path, vlm):
    """Save a session's full event history for later replay/resume."""
    try:
        SESSIONS_STORE.mkdir(parents=True, exist_ok=True)
        (SESSIONS_STORE / f"{sid}.json").write_text(json.dumps({
            "session_id": sid, "goal": goal, "dataset_path": dataset_path,
            "vlm": vlm, "status": handle.status, "events": handle.history,
        }, indent=2))
    except Exception:
        pass


@app.get("/api/sessions")
def list_saved_sessions():
    out = []
    if SESSIONS_STORE.exists():
        for f in sorted(SESSIONS_STORE.glob("*.json"), key=lambda p: -p.stat().st_mtime):
            try:
                d = json.loads(f.read_text())
                out.append({"session_id": d["session_id"], "goal": d.get("goal", ""),
                            "status": d.get("status"), "n_events": len(d.get("events", []))})
            except Exception:
                continue
    return out


@app.get("/api/session/{sid}/events")
def get_session_events(sid: str):
    """Replay: return a finished session's recorded events (from memory or disk)."""
    h = SESSIONS.get(sid)
    if h and h.history:
        return {"session_id": sid, "status": h.status, "events": h.history}
    f = SESSIONS_STORE / f"{sid}.json"
    if f.exists():
        return json.loads(f.read_text())
    return JSONResponse({"error": "unknown session"}, status_code=404)


@app.post("/api/session/{sid}/message")
async def session_message(sid: str, body: dict):
    """One chat input. Routes free text by session state (approve/refine/coach)."""
    h = SESSIONS.get(sid)
    if not h:
        return JSONResponse({"error": "unknown session"}, status_code=404)
    intent = h.message(body.get("text", ""))
    return {"ok": True, "intent": intent, "status": h.status}


@app.post("/api/session/{sid}/action")
async def session_action(sid: str, body: dict):
    """Explicit control buttons (pause/stop/approve/override)."""
    h = SESSIONS.get(sid)
    if not h:
        return JSONResponse({"error": "unknown session"}, status_code=404)
    h.submit_action(body.get("action", "continue"), body.get("text"), body.get("override"))
    return {"ok": True, "status": h.status}


@app.post("/api/session/{sid}/render_video")
async def render_video(sid: str):
    """
    Optional, user-triggered Phase-4 render: turn the committed joint trajectory
    into a Blender arm video. Only available when the scene had a .blend.
    Streams render_progress events; emits a final SESSION_DONE with the mp4 path.
    """
    from deep_viper.session.events import Event, EventType
    h = SESSIONS.get(sid)
    if not h:
        return JSONResponse({"error": "unknown session"}, status_code=404)
    if not h.blend_path or not Path(h.blend_path).exists():
        return JSONResponse({"error": "no .blend for this scene — video not available"}, status_code=400)
    if not h.run_dir or not (Path(h.run_dir) / "run_log.json").exists():
        return JSONResponse({"error": "no completed run to render"}, status_code=400)

    def _render():
        from deep_viper.scene.blender_renderer import render_session_video
        try:
            log = json.loads((Path(h.run_dir) / "run_log.json").read_text())
            ds = json.loads(Path(h.dataset_path).read_text())
            jt = log.get("joint_trajectory")
            if not jt:
                h.events.put(Event(EventType.INFO, "No joint trajectory to render.", {}))
                return
            box_name_by_id = {o["id"]: f"Box_{o['id']}_{o['color']}" for o in ds["objects"]}
            table_z = ds.get("table_z", 0.75)
            arm_base = [0.0, -(0.8 / 2 + 0.12), table_z]
            h.events.put(Event(EventType.RENDER_PROGRESS,
                               f"Rendering {len(jt)} frames in Blender…", {"frames": len(jt)}))
            res = render_session_video(
                scene_blend=str(Path(h.blend_path).resolve()),
                joint_trajectory=jt, box_name_by_id=box_name_by_id,
                arm_base=arm_base, table_z=table_z,
                assets_dir=str(REPO / "data" / "blender" / "assets"),
                out_dir=h.run_dir, samples=128, resolution=(1280, 720), fps=24,
            )
            if res.get("video"):
                h.events.put(Event(EventType.SESSION_DONE, "Video ready",
                                   {"video": res["video"], "n_frames": res["n_frames"]},
                                   image_path=None))
            else:
                h.events.put(Event(EventType.INFO, "Render finished but no video produced.", {}))
        except Exception as e:
            h.events.put(Event(EventType.INFO, f"Render error: {e}", {"error": str(e)}))

    threading.Thread(target=_render, daemon=True).start()
    return {"ok": True, "status": "rendering"}


@app.get("/api/video")
def get_video(path: str):
    p = Path(path)
    try:
        p.resolve().relative_to(REPO)
    except ValueError:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    if not p.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(p, media_type="video/mp4")


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
                h.submit_action(msg.get("action", "continue"), msg.get("text"), msg.get("override"))
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
