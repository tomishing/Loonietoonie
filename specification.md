# LoonieToonie — Specification

This is the product and technical specification for **LoonieToonie**. `CLAUDE.md` holds the working rules for coding; this file describes *what* the app is and *how it is designed*. Update it whenever a design decision changes.

## 1. Overview

LoonieToonie is a household expense app. Users scan receipts, track spending, and see their spending habits so they know how much they can save.

### Current priorities

1. Scan multilingual receipts (English, Japanese, etc.) and get the **date, items, prices, and currency** right.
   - Default currency: **USD**. Users can change it on the review screen.
2. Assign a category to each item based on its name.
3. Show items with their categories and prices, sorted by date.

### Platforms

- Web PWA (installable, view past data offline)
- Android via Capacitor (first native target)
- iOS via Capacitor (later)

## 2. Tech stack

| Layer | Technology | Folder |
| --- | --- | --- |
| Frontend | React PWA (Vite) + TypeScript | `frontend/` |
| Mobile wrapper | Capacitor (Android first, iOS later) | `frontend/android/` |
| OCR | On-device: Google ML Kit (Android) / Apple Vision (iOS); web fallback: Cloud Vision or Tesseract.js | `frontend/src/ocr/` |
| Backend / API | Python · FastAPI | `backend/app/` |
| AI parsing + chatbot | Anthropic Claude API (Haiku-class model for receipts) | `backend/app/ai/` |
| Database | Google Sheets in the user's own Drive (`gspread`) | `backend/app/sheets/` |
| Storage interface | `Repository` protocol, `SheetsRepository` implementation | `backend/app/storage/` |
| File storage | Google Drive, `LoonieToonie/receipts/` folder | `backend/app/drive/` |
| Auth | Google Sign-In (OAuth 2.0, `drive.file` scope) | `backend/app/auth/` |
| Charts | Recharts | `frontend/` |
| Deployment | Docker (backend), Vercel/Netlify (frontend) | `docker-compose.yml` |

## 3. Architecture

### Data ownership

All financial data lives in the user's own Google Drive:

- a spreadsheet named **LoonieToonie** (the database, see §4)
- a folder `LoonieToonie/receipts/` (receipt images)

The backend has no database server for financial data (see §8 for planned exceptions). OAuth uses only the `drive.file` scope, so the app can see only files it created.

### Authentication and sessions

Google Sign-In uses the OAuth 2.0 authorization-code flow with PKCE, run entirely by the backend:

1. The frontend navigates to `GET /api/auth/google`. The backend stores a random `state` and the PKCE verifier in a short-lived encrypted cookie (`lt_oauth`, 10 min, path `/api/auth`) and redirects to Google.
2. Scopes: `openid email profile https://www.googleapis.com/auth/drive.file`, with `access_type=offline` and `prompt=consent` so Google always returns a refresh token.
3. Google redirects to `GET /api/auth/callback`. The backend checks `state`, exchanges the code (with the PKCE verifier), and verifies the ID token (signature, audience, issuer, expiry, verified email).
4. Users can untick permissions on Google's consent screen. If `drive.file` was not granted, sign-in fails with `drive_permission_required`.
5. The backend finds or creates the user's spreadsheet and migrates it to the current schema (see *Storage setup* below).
6. The backend sets the session cookie and redirects to `FRONTEND_URL`. Failures redirect to `FRONTEND_URL/?auth_error=<reason>` (`access_denied`, `invalid_state`, `drive_permission_required`, `google_error`, `storage_error`), and the frontend shows a friendly message.

**Session storage (until storage stage 3):** there is no server-side session store. The session (`lt_session` cookie) holds the user's Google ID (`sub`), email, name, picture, **refresh token**, and spreadsheet ID, encrypted and authenticated with Fernet using a key derived from `SESSION_SECRET`. Cookies from before phase 3 have no spreadsheet ID and are rejected, so those users sign in again.

**Access tokens:** Google access tokens last about an hour. The backend keeps each one in server memory (keyed by a hash of the refresh token) until two minutes before it expires, then refreshes it. Nothing is written to disk; after a restart each user needs one extra refresh. The access token from sign-in seeds the cache.

- Cookie flags: `HttpOnly`, `SameSite=Lax`, `Secure` outside development, 30-day lifetime (`SESSION_MAX_AGE_DAYS`). The server also rejects tokens older than that.
- `GET /api/auth/me` returns only email, name, and picture. Tokens never reach JavaScript.
- `POST /api/auth/logout` clears the cookie. It does not revoke Google access; users can do that in their Google Account.
- Changing `SESSION_SECRET` signs everyone out.
- In stage 3 the refresh token moves to the encrypted server-side token store and the cookie keeps only a session reference.

**Deployment:** the cookie is first-party, so in production the frontend must reach the API on the same site, e.g. a Vercel/Netlify rewrite from `/api/*` to the backend (set `VITE_API_BASE_URL` empty). The Capacitor app (phase 9) may need a bearer-token variant, since its WebView origin differs from the API.

### Storage interface

Routes and services access data only through the `Repository` protocol in `backend/app/storage/`. `SheetsRepository` is the current implementation. A local cache or `PostgresRepository` can be added later without changing business logic.

Layers:

| Module | Role |
| --- | --- |
| `app/storage/repository.py` | `Repository` protocol and `StorageError` (API returns 502 for it) |
| `app/storage/sheets_repository.py` | `SheetsRepository`: reads tabs as rows keyed by header, so column order doesn't matter |
| `app/storage/setup.py` | `prepare_user_storage()`, called on every sign-in |
| `app/sheets/api.py` | `SheetsApi`: thin wrapper over the Drive and Sheets REST APIs (gspread's `HTTPClient`); tests use an in-memory fake |
| `app/sheets/schema.py` | Tab names, columns, default categories |
| `app/sheets/setup.py` | Find/create the spreadsheet and migrate its tabs |

Sheets API quotas are about 60 reads + 60 writes per minute per user, so:

- batch writes (`values.batchUpdate` / `append_rows`); never write row by row in a loop
- cache reads: each tab read is kept in server memory for 60 seconds per spreadsheet, and dropped when the backend writes to that tab. Edits made directly in the sheet show up within a minute.
- the spreadsheet is opened by ID without fetching its metadata on every request
- load tabs and compute summaries in Python (pandas); Sheets has no joins or SQL

### Storage setup (every sign-in)

1. Look for a non-trashed spreadsheet this app created, by the Drive app property `loonietoonie=spreadsheet`. `drive.file` only shows files the app created, and the marker means renaming or moving the file doesn't matter. If several exist, use the oldest.
2. If none exists, find or create the Drive folder **LoonieToonie** (`loonietoonie=root-folder`) and create the **LoonieToonie** spreadsheet inside it.
3. Migrate: add any missing tab, and append any missing column to the end of an existing tab's header row. Existing data, column order, and extra tabs or columns the user added are left alone. A new spreadsheet's empty starter tab ("Sheet1", or its localized name) becomes `receipts`. Header rows are frozen.
4. When the `categories` tab is created, it is filled with the default categories (§4). An existing `categories` tab is never reseeded.

First sign-in costs 2 Drive writes and 2 Sheets writes. Later sign-ins cost 2 reads and no writes when the schema is already current.

### Scan flow

1. User captures a receipt photo in the Scan page.
2. OCR runs **on the device** through one function, `recognizeReceipt()` in `frontend/src/ocr/`. Platform differences stay inside that folder.
3. The frontend sends the OCR text to `POST /api/receipts/scan`.
4. The backend asks Claude to parse the text into a draft receipt. Output is **strict JSON**, enforced with tool use / structured output and described by a Pydantic model.
5. The backend validates the draft (§5). If validation fails, it retries **once** with the receipt image attached. The image is never sent to Claude otherwise.
6. The user reviews and edits the draft (store, date, currency, items, categories), then saves with `POST /api/receipts`.
7. The backend generates UUIDs, appends rows to the sheet, and stores the image in Drive.

## 4. Data model (spreadsheet tabs)

| Tab | Columns |
| --- | --- |
| `receipts` | receipt_id, store, purchased_at, total, currency, image_link, created_at, updated_at, deleted |
| `items` | item_id, receipt_id, name, quantity, price, category, updated_at, deleted |
| `categories` | name, icon, updated_at, deleted |
| `budgets` | month, category, amount, updated_at, deleted |
| `settings` | key, value, updated_at |
| `ocr_labels` | receipt_id, field, model_value, corrected_value (opt-in) |

Rules:

- IDs (`receipt_id`, `item_id`) are UUIDs generated by the backend.
- `updated_at` is ISO 8601 UTC. Together with `deleted` it lets a local cache sync later with last-write-wins.
- `deleted` is `TRUE`/`FALSE`. Rows are never hard-deleted.
- Money is stored as a decimal value with an explicit currency; calculations never use floats.
- Purchase dates are `YYYY-MM-DD`.
- `ocr_labels` records user corrections to model output, only if the user opts in.
- Values are written as plain text (`RAW`), so dates, amounts, and `TRUE`/`FALSE` are stored exactly and are not reinterpreted by the spreadsheet's locale.

### Default categories

Created with a new `categories` tab. Users can edit them later.

| Icon | Name | Icon | Name |
| --- | --- | --- | --- |
| 🛒 | Groceries | 💊 | Health |
| 🍜 | Dining Out | 🧴 | Personal Care |
| 🧻 | Household | 👕 | Clothing |
| 🚗 | Transportation | 🎮 | Entertainment |
| 💡 | Utilities | 🧸 | Kids |
| 🏠 | Housing | 🐾 | Pets |
| | | 📦 | Other |
- Schema changes must be applied to existing spreadsheets by a migration that runs on login.

## 5. Receipt parsing and validation

### Saving a receipt (manual entry and confirmed scans)

`POST /api/receipts` and `PUT /api/receipts/{id}` accept the same body and enforce:

- `store`: 1–200 characters. `purchased_at`: a real date, at most 1 day in the future (time zones).
- `currency`: a 3-letter code, upper-cased. Defaults to `DEFAULT_CURRENCY` (`USD`) when omitted.
- `total`: 0 or more. Money values allow at most 3 decimal places; no NaN or infinity.
- `items`: 1–200 items. `price` is the **unit price**; the line amount is `price × quantity`. `quantity` is greater than 0 (default 1). A negative `price` is allowed for discount lines.
- Each item's `category` must exist in the user's `categories` tab. It is matched case-insensitively and saved with the tab's spelling.
- On edit, an `item_id` must belong to that receipt and appear once. Items without `item_id` are added; existing items left out are soft-deleted.
- The total is what the user enters; it is **not** required to equal the items' sum (tax, fees). The form pre-fills it with the items' sum and shows any difference.

Validation errors return 422 with FastAPI's `detail` list (`loc`, `msg`), which the frontend shows per field.

### Claude parsing (phase 5)

Claude returns a draft receipt as JSON matching a Pydantic schema. The backend checks:

- item prices (× quantity, minus discounts / plus tax) add up to the total
- the date is valid
- the currency is set (default `USD`)
- each item's category exists in the user's `categories` tab

Tests for the parser use sample OCR texts in several languages (at least English and Japanese).

## 6. API

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Health check (phase 1) |
| GET | `/api/auth/google`, `/api/auth/callback` | Google Sign-In (§3) |
| GET | `/api/auth/me` | Signed-in user (email, name, picture, `spreadsheet_url`); 401 if not signed in |
| GET | `/api/categories` | Active categories (`name`, `icon`) from the user's `categories` tab |
| POST | `/api/auth/logout` | Clear the session cookie |
| POST | `/api/receipts/scan` | OCR text (+ image on retry) → Claude → draft receipt |
| POST | `/api/receipts` | Save a receipt + items (§5); returns 201 with the saved receipt |
| GET | `/api/receipts` | Active receipts with their items, newest purchase first. Filters: `from`, `to` (`YYYY-MM-DD`, inclusive), `category` (receipts with at least one item in it) |
| GET | `/api/receipts/{id}` | One receipt with its items; 404 if missing or deleted |
| PUT | `/api/receipts/{id}` | Replace a receipt's fields and items (§5) |
| DELETE | `/api/receipts/{id}` | Soft-delete the receipt and its items; 204 |
| GET / PUT | `/api/budgets` | Monthly budgets per category |
| GET | `/api/summary?period=month` | Totals, by-category, budget vs actual, saved amount |
| POST | `/api/chat` | Spending chatbot |
| GET | `/api/export/csv` | CSV export |

Request and response bodies are Pydantic models. Money values (`total`, `price`, `quantity`) are JSON **strings** in responses (e.g. `"3.49"`); requests accept strings or numbers. Storage problems return 502; an expired Google grant returns 401.

Receipt response:

```json
{
  "receipt_id": "uuid", "store": "FreshMart", "purchased_at": "2026-10-01",
  "total": "7.47", "currency": "USD", "image_link": "",
  "created_at": "2026-10-13T09:30:00Z", "updated_at": "2026-10-13T09:30:00Z",
  "items": [{"item_id": "uuid", "name": "Milk", "quantity": "1", "price": "3.49", "category": "Groceries"}]
}
```

### Sheets cost per call

| Call | Reads | Writes |
| --- | --- | --- |
| List / get receipts | 0–2 (cached 60 s) | 0 |
| Create | 0–2 | 2 appends (receipts, items) |
| Update | 2 fresh reads | 1 batch rewrite + 1 append if items were added |
| Delete | 2 fresh reads | 1 batch rewrite |

Updates and deletes always re-read the tabs, so row numbers are correct even if the sheet was edited by hand. Rows are rewritten whole, keeping any extra columns the user added. Rows the backend can't parse (e.g. a hand-typed date that isn't `YYYY-MM-DD`) are skipped and logged.

## 7. Frontend

- Pages: Home, Scan (capture → review/edit → save), Transactions, Dashboard, Budget, Settings.
- Built so far (routes via React Router):
  - `/` Home: sign-in, spreadsheet link, categories.
  - `/transactions`: receipts with their items, newest first; filters for month (default: this month, or "All time") and category, kept in the URL; totals per currency.
  - `/receipts/new` and `/receipts/:id/edit`: the receipt form (store, date, currency, items with quantity, unit price and category, total). The total follows the items' sum until the user edits it. Edit also offers Delete.
- Money in the frontend is handled as decimal strings and bigint math (`src/lib/decimal.ts`), never JS floats.
- PWA: manifest + service worker. Users can view past data offline (IndexedDB cache is optional). The service worker never serves `/api/*` from cache.
- In development, Vite proxies `/api/*` to the backend. In production, `VITE_API_BASE_URL` points to the backend.
- UI style: simple and friendly, with cute icons and a girl mascot character. The current icon is a placeholder.

## 8. Storage roadmap

| Stage | When | Storage | Notes |
| --- | --- | --- | --- |
| 1 | Now (phases 1–5) | Google Sheets only | Simple, free, user-owned data |
| 2 | Phase 6 (dashboard) or when speed matters | + on-device cache: Capacitor SQLite (`@capacitor-community/sqlite`) on Android, Dexie/IndexedDB on web PWA | Instant loads + offline. Sheets stays the source of truth; sync with `updated_at` / `deleted` (last-write-wins) |
| 3 | Bank/card linking (phase 10) | + small **encrypted server-side token store** (server SQLite with backups, Supabase/Neon free tier, or Google Secret Manager) | Holds only `user_id`, encrypted Plaid/Flinks access tokens, Google refresh token, spreadsheet ID. **No financial data.** Bank transactions are still appended to the user's Sheet (dedupe by `transaction_id`) |
| 4 | Only if needed: sharing between users (family budgets), many users, or Sheets limits | Migrate to **PostgreSQL** (managed, Canadian region preferred) via `PostgresRepository`; Sheets becomes an export/sync option | Requires explicit approval and the privacy checklist below |

Bank tokens and other secrets are never stored in the user's Google Sheet.

### Privacy checklist (required before stage 4)

- TLS in transit, encryption at rest, extra column-level encryption for tokens and sensitive fields
- Row-Level Security (each user reads only their own rows)
- Minimal data: no bank passwords (aggregator only), no full card numbers, no receipt images in the database
- Two-factor authentication option for logins
- Data export + full deletion on account removal; limited backup retention
- PIPEDA / Quebec Law 25 compliance; clear privacy policy that says we never sell data or show ads

## 9. Configuration

Real values go in `.env` files, which are git-ignored. Only `.env.example` files are committed.

| File | Variables |
| --- | --- |
| `backend/.env` (or repo-root `.env`; `backend/.env` wins) | `APP_ENV`, `FRONTEND_ORIGINS`, `FRONTEND_URL`, `ANTHROPIC_API_KEY`, `ANTHROPIC_RECEIPT_MODEL`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, `SESSION_SECRET` (32+ chars), `SESSION_MAX_AGE_DAYS`, `DEFAULT_CURRENCY` |
| `frontend/.env` | `VITE_API_BASE_URL`, `VITE_DEV_BACKEND_URL` |

`VITE_*` variables are visible in the browser bundle, so they never hold secrets.

External setup: an Anthropic API key, a Google Cloud project with the Sheets + Drive APIs enabled, a Google OAuth client of type **Web application** (with `GOOGLE_REDIRECT_URI` under "Authorized redirect URIs"), and a Cloud Vision key (web OCR fallback only). The frontend does not need the Google client ID, because the backend runs the whole OAuth flow.

## 10. Development phases

| # | Phase | Status |
| --- | --- | --- |
| 1 | Scaffold: `frontend/` (Vite React PWA + TS), `backend/` (FastAPI), Docker Compose, `.env.example` | Done |
| 2 | Auth: Google Sign-In (`drive.file`) | Done |
| 3 | Storage: create the LoonieToonie spreadsheet + tabs on first login; `Repository` interface + `SheetsRepository` | Done |
| 4 | Receipts CRUD (manual entry first) | Done |
| 5 | Scan flow: Capacitor ML Kit OCR + Claude parsing + review page | |
| 6 | Dashboard & budgets (+ on-device cache if loading is slow; storage stage 2) | |
| 7 | Chatbot | |
| 8 | CSV export | |
| 9 | Capacitor Android build (needs Android Studio locally) | |
| 10 | Later: bank/card linking (Plaid / Flinks) with an encrypted token store (storage stage 3), saving/investment suggestions | |

## 11. Open questions

To be decided in the phase where they first matter:

- **Phase 5:** the exact Claude JSON schema for a draft receipt; how discounts and tax lines are represented; rounding tolerance for the total check; which receipt languages ship first.
- **Phase 6:** how "saved amount" is defined (budget minus actual, or income minus spending).
