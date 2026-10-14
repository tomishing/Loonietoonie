# LoonieToonie — Specification

This is the product and technical specification for **LoonieToonie**. `CLAUDE.md` holds the working rules for coding; this file describes *what* the app is and *how it is designed*. Update it whenever a design decision changes.

## 1. Overview

LoonieToonie is a household expense app. Users scan receipts and see **where their money goes**: their actual spending, by category and over time, and the patterns in it.

The current goal is understanding real spending, not saving. How much users save (budgets, saved amount, saving suggestions) is a later step that builds on these patterns (see phase 10).

### Current priorities

1. Scan multilingual receipts (English, Japanese, etc.) and get the **date, items, prices, and currency** right.
   - Default currency: **CAD** (`DEFAULT_CURRENCY`). Users can change it on the review screen.
   - Languages tested first: English, French (Québec), and Japanese.
2. Assign a category to each item based on its name.
3. Show items with their categories and prices, sorted by date.
4. Show spending patterns: where the money goes, how it changes month to month, and what is unusual.

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
5. Data migration: categories the app relies on (currently **Tax & Fees**) are appended to an existing `categories` tab if no row with that name exists. A row the user soft-deleted counts as existing, so it is not re-added.

First sign-in costs 2 Drive writes and 2 Sheets writes. Later sign-ins cost 3 reads and no writes when everything is already current.

### Scan flow

1. The user takes or picks a receipt photo on the Scan page. The browser downsizes it to at most 2000 px on the long edge (JPEG).
2. OCR runs **on the device** through one function, `recognizeReceipt()` in `frontend/src/ocr/`. Platform differences stay inside that folder.
   - Web/PWA: **Tesseract.js** with English, French, and Japanese language data (downloaded once, then cached by the browser).
   - Android: Google ML Kit, added in phase 9. iOS later: Apple Vision.
3. The frontend sends **only the OCR text** to `POST /api/receipts/scan`.
4. The backend asks Claude (`ANTHROPIC_RECEIPT_MODEL`, default `claude-haiku-5-5`) to read the text into a draft receipt. Output is **strict JSON** via structured outputs (`output_config.format` with a JSON schema whose `category` is an enum of the user's categories).
5. The backend normalizes and validates the draft (§5). Nothing is saved.
   - If a blocking issue is found and no photo was sent, the response has `needs_image: true`. The frontend then sends the same request **once** more with the photo (base64), and Claude reads the photo plus the text.
   - The photo leaves the device only for that retry, and never on a clean first reading. A second failure does not retry again; the issues are shown on the review screen.
   - Empty OCR text skips the first Claude call and asks for the photo directly.
6. The user reviews and edits the draft on the review screen (the receipt form, pre-filled, with the issues listed and the photo shown), then saves with `POST /api/receipts`.
7. After saving, the frontend uploads the photo with `PUT /api/receipts/{id}/image`. The backend stores it in Drive under `LoonieToonie/receipts/` (created on first use, found by the app property `loonietoonie=receipts-folder`) as `YYYY-MM-DD <store> <id prefix>.jpg`, and writes its link to `image_link`. If the upload fails, the receipt stays saved and the user is told.

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
- Schema changes must be applied to existing spreadsheets by a migration that runs on login.

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
| 🧾 | Tax & Fees | 📦 | Other |

**Tax & Fees** holds taxes and fees added on top of item prices (GST/HST/PST, TPS/TVQ, US sales tax, Japanese 外税, deposits, eco and bag fees, tips). The app relies on it, so it is added to existing spreadsheets on sign-in (§3).

## 5. Receipt parsing and validation

### Saving a receipt (manual entry and confirmed scans)

`POST /api/receipts` and `PUT /api/receipts/{id}` accept the same body and enforce:

- `store`: 1–200 characters. `purchased_at`: a real date, at most 1 day in the future (time zones).
- `currency`: a 3-letter code, upper-cased. Defaults to `DEFAULT_CURRENCY` (`CAD`) when omitted.
- `total`: 0 or more. Money values allow at most 3 decimal places; no NaN or infinity.
- `items`: 1–200 items. `price` is the **unit price**; the line amount is `price × quantity`. `quantity` is greater than 0 (default 1). A negative `price` is allowed for discount lines.
- Each item's `category` must exist in the user's `categories` tab. It is matched case-insensitively and saved with the tab's spelling.
- On edit, an `item_id` must belong to that receipt and appear once. Items without `item_id` are added; existing items left out are soft-deleted.
- The total is what the user enters; it is **not** required to equal the items' sum (tax, fees). The form pre-fills it with the items' sum and shows any difference.

Validation errors return 422 with FastAPI's `detail` list (`loc`, `msg`), which the frontend shows per field.

### Claude parsing (scan)

Claude's answer (schema in `app/ai/receipt_parser.py`): `store`, `purchased_at` (`YYYY-MM-DD` or null), `currency` (ISO 4217 or null), `total`, and `items` of `name`, `quantity`, `unit_price`, `category`. Money values are decimal **strings**, converted to `Decimal` by the backend.

How receipt lines map to items (in the prompt):

- `unit_price` is the price of one unit: "2 @ 1.99 3.98" is quantity 2, unit price 1.99. Weighed items are quantity 1 at the line amount, with the weight in the name.
- **Discounts**, coupons, and points redeemed are items with a negative price, in the category of the product they discount.
- **Taxes and fees added on top** of item prices are items in the **Tax & Fees** category (or Other if the user removed it). So category totals add up exactly to receipt totals.
- **Tax already included** in prices (Japanese 内税, VAT summaries) is informational and is not an item.
- `total` is the amount paid including tax; subtotals, tendered cash, change, and card lines are ignored.
- "$" means the default currency (CAD) unless the receipt shows US dollars. Japanese era years: 令和N年 = 2018 + N.

The backend normalizes the answer (amount formats like `4,99`, `¥1,080`, `100-`; unknown categories fall back to Other) and checks:

| Check | Issue code | Blocking (asks for the photo) |
| --- | --- | --- |
| No text recognized | `no_text` | yes (skips Claude) |
| No items | `no_items` | yes |
| Total or an item amount unreadable | `total_unreadable`, `item_unreadable` | yes |
| Σ quantity × price differs from the total by more than **2 of the currency's smallest unit** (0.02 CAD, ¥2, 0.002 KWD) | `total_mismatch` | yes |
| Date missing or invalid / in the future (> 1 day) | `date_missing` / `date_future` | yes |
| Date more than 10 years ago | `date_old` | no |
| No currency found (default used) | `currency_defaulted` | no |
| No store name | `store_missing` | no |

Claude errors, refusals, and cut-off answers return 502 ("enter it by hand"); a missing `ANTHROPIC_API_KEY` returns 503.

Tests: `tests/samples/` holds OCR texts in English (Ontario, HST), French (Québec, TPS/TVQ), Japanese (tax included, and tax added with a 令和 date), and US English (sales tax and tip), plus real Tesseract.js output for three of them. Unit tests use a fake Claude; `RUN_LIVE_CLAUDE=1 pytest tests/live` sends the samples to the real API (well under one cent per run).

## 6. API

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Health check (phase 1) |
| GET | `/api/auth/google`, `/api/auth/callback` | Google Sign-In (§3) |
| GET | `/api/auth/me` | Signed-in user (email, name, picture, `spreadsheet_url`); 401 if not signed in |
| GET | `/api/categories` | Active categories (`name`, `icon`) from the user's `categories` tab |
| POST | `/api/auth/logout` | Clear the session cookie |
| POST | `/api/receipts/scan` | `{ocr_text, image?, image_media_type?}` → Claude → `{draft, issues, needs_image, used_image}`. Nothing is saved (§3, §5) |
| PUT | `/api/receipts/{id}/image` | Body: the photo bytes (`image/jpeg`, `png`, or `webp`, ≤ 5 MB). Saves it to Drive and sets `image_link` |
| POST | `/api/receipts` | Save a receipt + items (§5); returns 201 with the saved receipt |
| GET | `/api/receipts` | Active receipts with their items, newest purchase first. Filters: `from`, `to` (`YYYY-MM-DD`, inclusive), `category` (receipts with at least one item in it) |
| GET | `/api/receipts/{id}` | One receipt with its items; 404 if missing or deleted |
| PUT | `/api/receipts/{id}` | Replace a receipt's fields and items (§5) |
| DELETE | `/api/receipts/{id}` | Soft-delete the receipt and its items; 204 |
| GET | `/api/summary?month=YYYY-MM&currency=CAD` | Spending patterns for one month and one currency (§7 Dashboard). Defaults: this month, `DEFAULT_CURRENCY` |
| GET / PUT | `/api/budgets` | Later (savings step, phase 10): monthly budgets per category |
| POST | `/api/chat` | Spending chatbot. Body: `{messages: [{role, content}]}` (text only, last one from the user, ≤ 40 messages, ≤ 4000 chars each). Streams newline-delimited JSON events (`text`, `status`, `reset`, `error`, `done`) (§7 Chat) |
| GET | `/api/export/csv` | CSV download (§7 Export). `kind=items` (default) or `receipts`; filters `from`, `to`, `category`, `currency` |

Request and response bodies are Pydantic models. Money values (`total`, `price`, `quantity`) are JSON **strings** in responses (e.g. `"3.49"`); requests accept strings or numbers. Storage problems return 502; an expired Google grant returns 401.

Receipt response:

```json
{
  "receipt_id": "uuid", "store": "FreshMart", "purchased_at": "2026-10-01",
  "total": "7.47", "currency": "CAD", "image_link": "",
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

- Pages: Home, Scan (capture → review/edit → save), Transactions, Dashboard ("Patterns"), Ask (chatbot), Settings. A Budget page comes with the savings step (phase 10).
- Built so far (routes via React Router):
  - `/` Home: sign-in, Scan and Enter-by-hand buttons, spreadsheet link, categories.
  - `/scan`: take or choose a photo, read it on the device with progress, send the text (and the photo once if needed), then open the review screen.
  - `/transactions`: **all** receipts with their items by default, newest purchase date first, grouped under month headings with each month's total (per currency). Optional filters for one month ("This month" shortcut) and category, kept in the URL. After saving, the list opens unfiltered and scrolls to and highlights the saved receipt, so receipts dated in other months are never hidden.
  - `/receipts/new` and `/receipts/:id/edit`: the receipt form (store, date, currency, items with quantity, unit price and category, total). The total follows the items' sum until the user edits it. Edit also offers Delete. The currency defaults to `DEFAULT_CURRENCY` (from `/api/auth/me`).
  - Review screen: `/receipts/new` with a scan result in router state. It is pre-filled from the draft, lists the issues, shows the photo, keeps the scanned total as read, and uploads the photo after saving.
  - Transactions show a 📷 link for receipts with a photo, and "Export CSV: items · receipts" links for the current filters.
- Money in the frontend is handled as decimal strings and bigint math (`src/lib/decimal.ts`), never JS floats.

### Export (CSV)

`GET /api/export/csv` downloads the user's receipts for Excel, Numbers, or Google Sheets. Filename: `loonietoonie-<kind>-<today>.csv`.

- `kind=items` (default): one row per item: `date, store, item, category, quantity, unit_price, amount (= quantity × unit_price), currency, receipt_total, receipt_id`. When a receipt's total differs from its items, a `(not itemized)` row keeps `amount` adding up to the receipt totals. With a `category` filter, only that category's items are included (and no `(not itemized)` rows).
- `kind=receipts`: one row per receipt: `date, store, total, currency, items (count), categories, photo (Drive link), receipt_id`. A `category` filter keeps receipts that have at least one item in it.
- Newest first; deleted rows are never exported. All currencies unless `currency` is given (each row says its currency; nothing is converted).
- UTF-8 **with a byte-order mark**, so Excel shows Japanese and French text correctly; `\r\n` line endings; ISO dates; amounts as plain decimals (`-1.00` for discounts).
- **CSV injection:** free-text cells (store, item, category) starting with `=`, `+`, `-`, `@`, tab, or CR get a leading `'`, so spreadsheet apps show them as text instead of running them as formulas. Number and date columns are never altered.

### Chat ("Ask", `/ask`)

Purpose: answer questions about actual spending ("What did I spend most on this month?", "今月の外食はいくら？") from the user's own receipts.

- **Model:** `ANTHROPIC_CHAT_MODEL` (default `claude-opus-5-5`, effort `medium`). `claude-haiku-5-5` is a much cheaper option. Refusal fallbacks are on: `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`) re-runs a safety-declined request on Anthropic's recommended model; the frontend drops any partial text on a `reset` event.
- **Numbers come from tools, not from the model's arithmetic.** Three read-only tools run pandas over the user's receipts (`app/services/spending_queries.py`), and their inputs are validated with Pydantic before running:
  - `spending_breakdown`: total and rows for a date range, grouped by category, store, month, item, or weekday; optional category, store, and item filters
  - `find_receipts`: matching receipts with their items (newest first, up to 30) plus the count and total of all matches
  - `month_overview`: the dashboard numbers for one month (same code as `/api/summary`)
- **Prompt:** a stable system prompt (cached) plus per-user facts after the cache breakpoint: today, default currency, receipt counts per currency, first/last receipt date, and category names. Rules in the prompt:
  - one currency at a time
  - "so far" and same-days comparisons for the month in progress
  - answer in the user's language
  - short answers, using `- ` bullets and `**bold**` only (the frontend renders just these)
  - no savings lectures
- **Loop:** streamed, at most 6 tool rounds per answer. Each tool round appends to the history unchanged (thinking blocks stay valid). The answer is checked for refusals and cut-off tool input before any tool runs, and unparseable tool JSON is retried at most twice.
- **No server-side chat storage.** The conversation lives in the browser tab (`sessionStorage`) and is sent as plain text with each request. Earlier answers are replayed without thinking blocks, which is allowed.
- **Privacy:** questions and the spending data the tools return are sent to Claude (Anthropic). The Ask page says so.
- **Tests:** a fake streaming client covers the loop; `RUN_LIVE_CLAUDE=1 pytest tests/live` asks the real model questions in English and Japanese about known receipts (a few cents per run).

### Dashboard ("Patterns", `/dashboard`)

Purpose: see actual spending patterns. Computed on the backend with pandas (`app/services/summary.py`) from all receipts, served by `GET /api/summary`.

- **One currency at a time.** The dashboard shows `DEFAULT_CURRENCY` (CAD); a switcher appears when there are receipts in other currencies. Amounts are never converted or added across currencies.
- **Period:** one month (‹ › and a month picker; no future months). For the **month in progress**, every comparison uses the same days (1 to today) of earlier months, so a half month isn't compared with full months.
- **Usual month:** the average of the previous 3 months, counting only months since the first receipt in that currency (no invented zeros). No history → no comparisons and no notable changes. Averages are rounded to 0.01.
- **Views:**
  1. *Total spent* (the one hero figure), with % change vs last month and vs the usual month, and the receipt count.
  2. *Notable changes*, in plain words:
     - a category ≥ 25% above or below its usual amount, where the difference is also ≥ 5% of the usual month's total spending
     - a category with spending for the first time (≥ 5% of this month)
     - up to 3 stores visited for the first time ever
     - Tax & Fees is not reported (it follows other spending). At most 5 entries.
  3. *By category*:
     - one horizontal bar per category, all in the same color (a single series), with a tick for the usual month
     - each row shows the amount and the change vs usual; categories that dropped to zero stay listed
     - hover or keyboard focus shows this month, last month, and the usual month
  4. *Last 12 months*:
     - stacked columns of the top 5 categories (by 12-month spending), with smaller categories folded into a gray "Other categories" (`(other categories)` in the API, never equal to a real category name)
     - a legend, a tooltip listing every series, and a "Show table" view with all the numbers
  5. *Top stores* (spent, visits) and *Bought most often* (times, spent; Tax & Fees excluded) for the month, as tables.
- **Not itemized:** when a receipt's total differs from its items (e.g. tax typed only into the total), the difference is shown as "Not itemized" (`(not itemized)`), so category totals always add up to the month's total.
- **Charts:**
  - categorical slots 1–5 of the validated reference palette (blue, orange, aqua, yellow, magenta) in fixed order, gray for "Other categories"
  - thin marks: bars ≤ 24 px, a 2 px surface gap between stacked segments, hairline grid
  - text always in text colors, never in series colors
  - three slots fall below 3:1 contrast on the light surface, so the legend and table view are required
- **Loading:**
  - while another month loads, the previous one stays visible, dimmed
  - Recharts loads only when the dashboard opens (lazy route)
  - an on-device cache (storage stage 2) is not needed yet: the backend's 60-second read cache keeps the dashboard quick. Revisit if loading feels slow.
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
| `backend/.env` (or repo-root `.env`; `backend/.env` wins) | `APP_ENV`, `FRONTEND_ORIGINS`, `FRONTEND_URL`, `ANTHROPIC_API_KEY`, `ANTHROPIC_RECEIPT_MODEL`, `ANTHROPIC_CHAT_MODEL`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, `SESSION_SECRET` (32+ chars), `SESSION_MAX_AGE_DAYS`, `DEFAULT_CURRENCY` |
| `frontend/.env` | `VITE_API_BASE_URL`, `VITE_DEV_BACKEND_URL` |

`VITE_*` variables are visible in the browser bundle, so they never hold secrets.

External setup: an Anthropic API key, a Google Cloud project with the Sheets + Drive APIs enabled, a Google OAuth client of type **Web application** (with `GOOGLE_REDIRECT_URI` under "Authorized redirect URIs"), and nothing for web OCR (Tesseract.js runs in the browser). The frontend does not need the Google client ID, because the backend runs the whole OAuth flow.

## 10. Development phases

| # | Phase | Status |
| --- | --- | --- |
| 1 | Scaffold: `frontend/` (Vite React PWA + TS), `backend/` (FastAPI), Docker Compose, `.env.example` | Done |
| 2 | Auth: Google Sign-In (`drive.file`) | Done |
| 3 | Storage: create the LoonieToonie spreadsheet + tabs on first login; `Repository` interface + `SheetsRepository` | Done |
| 4 | Receipts CRUD (manual entry first) | Done |
| 5 | Scan flow: on-device OCR (Tesseract.js on web; ML Kit moves to phase 9) + Claude parsing + review page | Done |
| 6 | Dashboard: spending patterns (+ on-device cache if loading is slow; storage stage 2) | Done |
| 7 | Chatbot ("Ask"): streamed answers from read-only spending tools | Done |
| 8 | CSV export (items or receipts, current filters, injection-safe) | Done |
| 9 | Capacitor Android build (needs Android Studio locally), including ML Kit OCR behind `recognizeReceipt()` | |
| 10 | Later: savings step (monthly budgets per category with budget vs actual, saved amount, saving/investment suggestions); bank/card linking (Plaid / Flinks) with an encrypted token store (storage stage 3) | |

## 11. Open questions

To be decided in the phase where they first matter:

- **Phase 10 (savings step):** how "saved amount" is defined (budget minus actual, or income minus spending).
