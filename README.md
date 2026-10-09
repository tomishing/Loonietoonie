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

### Google Sign-In

1. In [Google Cloud Console](https://console.cloud.google.com/), enable the **Google Sheets API** and **Google Drive API**.
2. Under **APIs & Services → OAuth consent screen**, set up the app and add the scopes `openid`, `email`, `profile`, and `.../auth/drive.file`. While the app is in "Testing", add your Google account as a test user.
3. Under **APIs & Services → Credentials**, create an **OAuth client ID** of type **Web application** and add this **Authorized redirect URI**:
   `http://localhost:8000/api/auth/callback`
4. Put the client ID and secret in `.env` as `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.
5. Add a session secret:
   `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` → `SESSION_SECRET=...`

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
cd backend && .venv/bin/ruff check . && .venv/bin/mypy --strict app && .venv/bin/pytest
cd frontend && npm run build && npm run preview   # production build with service worker
```
