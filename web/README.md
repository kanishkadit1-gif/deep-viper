# Deep VIPER Co-Pilot — Web Frontend

Interactive co-pilot for the planning harness: watch the VLM plan in real time,
coach it inline, pause/stop, and (soon) edit artifacts + branch sessions.

## Architecture
- **Backend** (`web/server.py`): FastAPI. Wraps `run_session` in a background
  thread per session via a `QueueController` (see `deep_viper/session/bridge.py`).
  Streams events over WebSocket; accepts control actions (pause/resume/stop/
  correction). Serves scene/run images and the built UI.
- **Frontend** (`web/frontend/`): React + Tailwind + Vite. Live event timeline,
  scene picker, goal composer, stage viewer, and the VLM-coaching control panel.

## Run (development — two terminals)

Terminal 1 — backend:
```
.\venv\Scripts\python.exe -m uvicorn web.server:app --reload --port 8000
```

Terminal 2 — frontend dev server (proxies /api and /ws to :8000):
```
cd web/frontend
npm install      # first time
npm run dev      # opens http://localhost:5173
```

## Run (production — single server)
```
cd web/frontend && npm run build      # produces dist/
.\venv\Scripts\python.exe -m uvicorn web.server:app --port 8000
# open http://127.0.0.1:8000  (backend serves the built UI)
```

## Requirements
- Python deps: `fastapi`, `uvicorn[standard]`, `websockets` (in requirements.txt)
- Node 20.19+ / 22.12+ (Vite 8). Tested on Node 24.
- For the local-model path: LM Studio serving Qwen3-VL-4B at `http://127.0.0.1:1367/v1`.
