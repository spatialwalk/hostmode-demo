# SpatialReal Host Mode Demo

Minimal Host Mode demo for SpatialReal avatars without LiveKit.

- Frontend: `Vite + TypeScript + @spatialwalk/avatarkit`
- Backend: `FastAPI + WebSocket + avatarkit`
- Agent: Doubao realtime voice model
- Flow: browser sends mic/text -> backend talks to Doubao -> backend streams avatar audio and frames back to the browser

## Requirements

- Node.js 18+
- Python 3.11+
- Valid SpatialReal avatar credentials
- Valid Doubao realtime voice credentials

## Setup

Copy env template:

```bash
cp env.example .env
```

Required env values:

- `SPATIALREAL_AVATAR_APP_ID`
- `SPATIALREAL_AVATAR_ID`
- `SPATIALREAL_AVATAR_API_KEY`
- `SPATIALREAL_AVATAR_CONSOLE_ENDPOINT`
- `SPATIALREAL_AVATAR_INGRESS_ENDPOINT`
- `DOUBAO_E2E_APP_ID`
- `DOUBAO_E2E_ACCESS_TOKEN`

## Run

Start backend:

```bash
cd backend
uv sync
uv run uvicorn app.main:app --host 127.0.0.1 --port 8765
```

Start frontend:

```bash
cd frontend
npm i
npm run dev
```

Open `http://127.0.0.1:5173`.

## Use

- Click `Initialize`
- Send a text prompt or start the mic
- The avatar renders in Host Mode in the browser

## Notes

- Frontend does not use a session token for Host Mode playback
- Browser-to-backend audio is sent over JSON + base64 for simplicity
