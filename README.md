# LoonieToonie

Household expense app: scan receipts, track spending, see how much you can save.
See `specification.md` for the full spec.

## Requirements

- Node.js 20.19+ or 22.12+ and npm (required by Vite 8)
- Python 3.12+
- Docker (optional, for the backend container)

## Setup

```bash
cp backend/.env.example backend/.env     # fill in keys
cp frontend/.env.example frontend/.env
```

The backend also reads a repo-root `.env`; values in `backend/.env` take precedence.

## Run the backend

With Docker:

```bash
docker compose up --build
```

Or locally:

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/uvicorn app.main:app --reload
```

API: http://localhost:8000/api/health · docs: http://localhost:8000/docs

## Run the frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. In dev, `/api/*` is proxied to the backend on port 8000.

## Tests and builds

```bash
cd backend && .venv/bin/pytest
cd frontend && npm run build && npm run preview   # production build with service worker
```
