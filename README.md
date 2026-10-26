# LoonieToonie

Household expense app: scan your receipts and see where your money goes — spending by category, month-to-month changes, and what's unusual.
See `specification.md` for the full spec.

## Requirements

- Node.js 22.22+ and npm (required by React Router 8 and Vite 8)
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

### Receipt scanning

Scanning needs `ANTHROPIC_API_KEY` in `.env`. Receipts are read by `claude-haiku-5-5` (`ANTHROPIC_RECEIPT_MODEL`). OCR runs in the browser with Tesseract.js, which downloads English, French, and Japanese language data the first time you scan. The default currency is CAD (`DEFAULT_CURRENCY`).

### Ask (spending chatbot)

Uses the same `ANTHROPIC_API_KEY`. Answers come from `claude-opus-5-5` (`ANTHROPIC_CHAT_MODEL`), which looks up numbers in your receipts with read-only tools; set `ANTHROPIC_CHAT_MODEL=claude-haiku-5-5` for a much cheaper model. Chats are not stored on the server.

### Bank and card statements

No setup needed. Account → **Accounts & cards** → add an account, then **Import** a statement file downloaded from the bank's website (CSV, or OFX/QFX). New merchants are categorized with the same `ANTHROPIC_API_KEY` (`ANTHROPIC_RECEIPT_MODEL`); without a key they import as Other. To try it without a real statement, use **Try a sample file** on the Import page (`frontend/public/sample-statement.csv`, made-up CIBC-style card transactions).

### Bank and card linking (Plaid)

Optional, and on the web app only: the Android app never includes Plaid, and the web app offers it only to the Google accounts in `PLAID_ALLOWED_EMAILS` (the Account page hides it for everyone else).

1. Create an account at [dashboard.plaid.com](https://dashboard.plaid.com) and copy the client ID and the **Sandbox** secret (Developers → Keys).
2. In `.env`, set `PLAID_CLIENT_ID`, `PLAID_SECRET`, `PLAID_ENV=sandbox`, `PLAID_ALLOWED_EMAILS` (e.g. `["you@example.com"]`), and `TOKEN_ENCRYPTION_KEY`, generated with `python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"`.
3. Restart the backend (`docker compose up -d backend`).

In Sandbox, link a test bank such as "First Platypus Bank" with username `user_good` and password `pass_good`. Access tokens are stored encrypted in `backend/data/tokens.sqlite3` (git-ignored); back up that folder and the key together. Losing the key means linking every bank again.

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

The first sign-in creates a **LoonieToonie** folder in your Google Drive with a **LoonieToonie** spreadsheet inside (tabs: receipts, items, categories, subcategories, bank_accounts, bank_transactions, imports, settings, ocr_labels, and a few more). The Account page links to it and lists your categories.

## Android app (Capacitor)

Needs Android Studio (Android SDK in `~/Android/Sdk`) and an Android phone with **USB debugging** on.

```bash
# 1. Backend running on this computer (docker compose up, or uvicorn), as above.
# 2. Phone connected by USB. Let the phone reach this computer's backend as its own localhost:
~/Android/Sdk/platform-tools/adb reverse tcp:8000 tcp:8000
# 3. Build, install, and start the app on the phone:
cd frontend
npm run android:run        # or: npm run android:build, then open frontend/android in Android Studio
```

- Sign-in opens Google in the phone's browser and comes back to the app.
- Run the `adb reverse` command again whenever you reconnect the phone or adb restarts. Otherwise the app says it can't reach the server.
- OCR runs on the phone with Google ML Kit (English, French, Japanese).
- To inspect the app's WebView, use desktop Chrome → `chrome://inspect` (debug builds).

### Without the USB cable (Tailscale)

The phone can reach the backend on this computer over your private Tailscale network, on Wi-Fi or mobile data. The computer must be on; nothing is exposed to the public internet.

1. Install Tailscale on the computer (`curl -fsSL https://tailscale.com/install.sh | sh`, `sudo tailscale up`, `sudo tailscale set --operator=$USER`) and on the phone (Play Store), signed in to the same account. In the admin console, turn on MagicDNS and HTTPS certificates.
2. Publish the backend on the computer's HTTPS name: `tailscale serve --bg 8000` → `https://<computer>.<tailnet>.ts.net` (tailnet only; the setting survives restarts).
3. In `frontend/.env.android.local` (git-ignored): `VITE_API_BASE_URL=https://<computer>.<tailnet>.ts.net`. The `android:*` scripts build with `--mode android`, which reads it; the website keeps using the Vite proxy.
4. In `.env`: `APP_GOOGLE_REDIRECT_URI=https://<computer>.<tailnet>.ts.net/api/auth/callback`, and add the same URL to the Google OAuth client's **Authorized redirect URIs**. Restart the backend.
5. Install once with `npm run android:run` (USB or wireless debugging). After that, keep Tailscale on in the phone and the app works anywhere.

## Tests and builds

```bash
cd backend && .venv/bin/ruff check . && .venv/bin/mypy --strict app && .venv/bin/pytest
cd backend && RUN_LIVE_CLAUDE=1 .venv/bin/pytest tests/live -v   # sample receipts + chatbot questions through the real Claude API (a few cents)
cd frontend && npm run lint && npm test && npm run build && npm run preview   # production build with service worker
```
