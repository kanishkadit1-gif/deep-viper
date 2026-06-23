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
from deep_viper.session.bridge import SessionHandle, QueueController
from deep_viper.session.events import EventType

REPO = Path(__file__).resolve().parent.parent
SCENES_DIR = REPO / "data" / "blender" / "scenes"
RUNS_DIR = REPO / "runs"
UPLOADS_DIR = REPO / "data" / "uploads"
SESSIONS_STORE = RUNS_DIR / "_sessions"   # persisted session records (replay/resume)
FRONTEND_DIST = REPO / "web" / "frontend" / "dist"

app = FastAPI(title="Deep VIPER Co-Pilot")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])

# --------------------------------------------------------------------------- #
# Session registry — ONE place to resolve a session id, so every endpoint
# treats live (in-memory) and persisted (on-disk) sessions consistently.
#
#   live_session(sid)    -> the in-memory handle or None. Use for CONTROL
#                           (message/action): a dead session has no control
#                           queue, so None correctly means "not controllable".
#   resolve_session(sid) -> live handle, else a handle rehydrated from the
#                           persisted record (and registered). Use for DATA /
#                           render / websocket: these work on any session.
# --------------------------------------------------------------------------- #
SESSIONS: dict[str, SessionHandle] = {}


def live_session(sid: str) -> SessionHandle | None:
    return SESSIONS.get(sid)


def resolve_session(sid: str) -> SessionHandle | None:
    h = SESSIONS.get(sid)
    if h is not None:
        return h
    rec_path = SESSIONS_STORE / f"{sid}.json"
    if not rec_path.exists():
        return None
    rec = json.loads(rec_path.read_text())
    h = SessionHandle(session_id=sid)
    h.goal = rec.get("goal", "")
    h.status = rec.get("status", "done")
    h.dataset_path = rec.get("dataset_path", "")
    h.vlm = rec.get("vlm")
    h.history = rec.get("events", [])
    h._record = rec   # stash for lazy Session rehydration on first new turn
    # Derive blend_path from the dataset and run_dir from the events when the
    # persisted top-level fields are absent (so render works for any vintage).
    h.blend_path = rec.get("blend_path") or _blend_from_dataset(h.dataset_path)
    h.run_dir = rec.get("run_dir") or _last_run_dir_from_events(h.history)
    SESSIONS[sid] = h   # register so WS / render can attach
    return h


def _blend_from_dataset(dataset_path: str) -> str:
    try:
        return json.loads(Path(dataset_path).read_text()).get("blend_path", "")
    except Exception:
        return ""


def _last_run_dir_from_events(events: list) -> str:
    for e in reversed(events or []):
        rd = (e.get("payload") or {}).get("run_dir")
        if rd:
            return rd
    return ""


def _rehydrate_live_session(h: SessionHandle) -> None:
    """Reconstruct a runnable multi-turn Session from the persisted record so a
    reopened session accepts new turns exactly like a live one."""
    from deep_viper.session.session import Session, TurnRecord
    from deep_viper.planning.harness import load_scene
    rec = getattr(h, "_record", {}) or {}
    cfg = load_config(vlm_profile=h.vlm)
    scene = load_scene(h.dataset_path)
    if rec.get("world_state"):
        scene.apply_world_state(rec["world_state"])
    transcript = [TurnRecord(**t) for t in rec.get("transcript", [])]
    h.session = Session(cfg, scene, h.dataset_path,
                        blend_path=h.blend_path, transcript=transcript,
                        conflict_default=rec.get("conflict_default"))
    h.session.load_corrections()


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
    conflict_default = body.get("conflict_default")  # "s" stack | else clear blocker
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
    handle.vlm = vlm
    cfg = load_config(vlm_profile=vlm)

    from deep_viper.session.session import Session
    from deep_viper.planning.harness import load_scene
    scene = load_scene(dataset_path)
    handle.session = Session(cfg, scene, dataset_path, blend_path=handle.blend_path,
                             conflict_default=conflict_default)
    handle.session.load_corrections()

    _launch_turn(sid, goal)
    return {"session_id": sid, "status": "running"}


def _launch_turn(sid: str, goal: str) -> None:
    """Run one turn of the session in a background thread."""
    handle = SESSIONS[sid]

    def _run():
        handle.status = "running"
        handle.stopped.clear()
        ctl = QueueController(handle)
        try:
            handle.session.run_turn(goal, controller=ctl)
            if handle.status not in ("aborted",):
                handle.status = "done"
        except Exception as e:
            handle.status = "error"
            handle.events.put(_err_event(str(e)))
        finally:
            _persist_session(sid, handle)
            handle.events.put(_sentinel_event())

    t = threading.Thread(target=_run, daemon=True)
    handle.thread = t
    t.start()


def _persist_session(sid, handle):
    """Persist events + world state + transcript for replay AND resume."""
    try:
        SESSIONS_STORE.mkdir(parents=True, exist_ok=True)
        sess = handle.session
        rec = {
            "session_id": sid, "goal": handle.goal,
            "dataset_path": handle.dataset_path, "blend_path": handle.blend_path,
            "vlm": getattr(handle, "vlm", None), "status": handle.status,
            "run_dir": handle.run_dir, "events": handle.history,
        }
        if sess is not None:
            rec["world_state"] = sess.scene.world_state()
            rec["transcript"] = [t.__dict__ for t in sess.transcript]
            rec["conflict_default"] = sess.conflict_default
        (SESSIONS_STORE / f"{sid}.json").write_text(json.dumps(rec, indent=2))
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
    """Replay: a session's recorded events (data access — live or persisted)."""
    h = resolve_session(sid)
    if not h:
        return JSONResponse({"error": "unknown session"}, status_code=404)
    return {"session_id": sid, "status": h.status, "events": h.history}


@app.post("/api/session/{sid}/message")
async def session_message(sid: str, body: dict):
    """One chat input. Routes by state; on an idle/reopened session a message
    starts a NEW TURN (reopened == live, multi-turn)."""
    text = body.get("text", "")
    h = resolve_session(sid)
    if not h:
        return JSONResponse({"error": "unknown session"}, status_code=404)
    if h.session is None:
        _rehydrate_live_session(h)
    intent = h.message(text)
    if intent == "new_turn":
        _launch_turn(sid, text)
        return {"ok": True, "intent": "new_turn", "status": "running"}
    return {"ok": True, "intent": intent, "status": h.status}


@app.post("/api/session/{sid}/action")
async def session_action(sid: str, body: dict):
    """Explicit control buttons (pause/stop/approve/override) — LIVE session only."""
    h = live_session(sid)
    if not h:
        return JSONResponse({"error": "session is not running"}, status_code=409)
    h.submit_action(body.get("action", "continue"), body.get("text"), body.get("override"))
    return {"ok": True, "status": h.status}


@app.post("/api/session/{sid}/render_video")
async def render_video(sid: str):
    """
    Optional, user-triggered Phase-4 render -> Blender arm video. Streams
    RENDER_PROGRESS events (done/total), is interruptible, and supports a
    quality knob (samples). Only available when the scene had a .blend.
    """
    from deep_viper.session.events import Event, EventType
    h = resolve_session(sid)
    if not h:
        return JSONResponse({"error": "unknown session"}, status_code=404)
    if not h.blend_path or not Path(h.blend_path).exists():
        return JSONResponse({"error": "no .blend for this scene — video not available"}, status_code=400)
    if not h.run_dir or not (Path(h.run_dir) / "run_log.json").exists():
        return JSONResponse({"error": "no completed run to render"}, status_code=400)

    # EEVEE rasterizer: fast, no ray-traced shadows/reflections (by design).
    samples, resolution, engine = 64, (1280, 720), "EEVEE"
    h.render_cancel.clear()

    def _render():
        from deep_viper.pipeline import Renderer
        from deep_viper.domain import JointTrajectory, JointFrame
        from deep_viper.planning.harness import load_scene
        try:
            log = json.loads((Path(h.run_dir) / "run_log.json").read_text())
            ds = json.loads(Path(h.dataset_path).read_text())
            jt_raw = log.get("joint_trajectory")
            if not jt_raw:
                h.events.put(Event(EventType.INFO, "No joint trajectory to render.", {}))
                return
            scene = load_scene(h.dataset_path)
            jt = JointTrajectory(frames=[JointFrame(**f) for f in jt_raw])
            box_name_by_id = {o["id"]: f"Box_{o['id']}_{o['color']}" for o in ds["objects"]}
            import time as _t
            t0 = _t.time()

            def progress(done, total):
                pct = round(100 * done / max(total, 1))
                elapsed = int(_t.time() - t0)
                eta = int(elapsed / done * (total - done)) if done else None
                h.events.put(Event(EventType.RENDER_PROGRESS,
                    f"Rendering {done}/{total} frames ({pct}%)",
                    {"done": done, "total": total, "pct": pct,
                     "elapsed_s": elapsed, "eta_s": eta}))

            res = Renderer().render_video(
                scene, jt, h.blend_path, Path(h.run_dir), box_name_by_id,
                samples=samples, resolution=resolution, engine=engine,
                progress_cb=progress,
                should_cancel=lambda: h.render_cancel.is_set(),
                on_process=lambda p: setattr(h, "render_proc", p))
            if res.get("cancelled"):
                h.events.put(Event(EventType.INFO, "Render cancelled.", {"cancelled": True}))
            elif res.get("video"):
                h.events.put(Event(EventType.SESSION_DONE, "Video ready",
                                   {"video": res["video"], "n_frames": res["n_frames"]}))
            else:
                h.events.put(Event(EventType.INFO, "Render finished but no video produced.", {}))
        except Exception as e:
            h.events.put(Event(EventType.INFO, f"Render error: {e}", {"error": str(e)}))
        finally:
            h.render_proc = None

    threading.Thread(target=_render, daemon=True).start()
    return {"ok": True, "status": "rendering", "samples": samples}


@app.post("/api/session/{sid}/render_cancel")
async def render_cancel(sid: str):
    h = SESSIONS.get(sid)
    if not h:
        return JSONResponse({"error": "unknown session"}, status_code=404)
    h.render_cancel.set()
    return {"ok": True}


@app.delete("/api/session/{sid}")
async def delete_session(sid: str):
    h = SESSIONS.pop(sid, None)
    if h is not None:
        h.stopped.set()
        h.render_cancel.set()
    f = SESSIONS_STORE / f"{sid}.json"
    if f.exists():
        f.unlink()
    return {"ok": True}


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
    h = resolve_session(sid)
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
