# LoonieToonie — Specification

This is the product and technical specification for **LoonieToonie**. `CLAUDE.md` holds the working rules for coding; this file describes *what* the app is and *how it is designed*. Update it whenever a design decision changes.

## 1. Overview

LoonieToonie is a household expense app. Users scan receipts and see **where their money goes**: their actual spending, by category and over time, and the patterns in it.

**Name (decided 2026-10-29):** the full name is **LoonieToonie Tracker**, used for the Play listing, the browser title and the PWA `name`, the login screen, the GitHub Pages site, and the Google consent screen. **LoonieToonie** stays the short name: the Android launcher label, the PWA `short_name`, the app header, and running text. The app ID `com.loonietoonie.app` (also the sign-in redirect scheme) and the Drive folder and spreadsheet name `LoonieToonie` stay unchanged, so existing users keep their data.

The goal is understanding real spending, not saving: receipts and bank and card transactions (imported from statement files the user downloads from their bank, phase 11; for invited households also synced through Plaid on the web app, phase 12) show where the money goes. Budgets, saved amount, and saving suggestions are **out of scope** (a planned savings phase was dropped).

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
| OCR | On-device: Google ML Kit (Android app, local Capacitor plugin; receipt capture with the ML Kit Document Scanner) / Apple Vision (iOS, later); web/PWA: Tesseract.js | `frontend/src/ocr/`, `frontend/src/capture/`, `frontend/android/…/ReceiptOcrPlugin.java`, `DocumentScannerPlugin.java` |
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
   **Invite-only launch:** when `ALLOWED_EMAILS` (a JSON list, compared case-insensitively; `PLAID_ALLOWED_EMAILS` are always included) is not empty, a Google account that isn't on it fails with `not_invited` right after the ID token is verified, before any spreadsheet is created; on Android the deep link carries the same error. Every authenticated request checks the list too, so removing an email ends that person's session (401, back to sign-in). An empty list lets anyone sign in; outside development the backend logs a warning at startup.
5. The backend finds or creates the user's spreadsheet and migrates it to the current schema (see *Storage setup* below).
6. The backend sets the session cookie and redirects to `FRONTEND_URL`. Failures redirect to `FRONTEND_URL/?auth_error=<reason>` (`access_denied`, `invalid_state`, `not_invited`, `drive_permission_required`, `google_error`, `storage_error`), and the frontend shows a friendly message.

**Session storage (until storage stage 3):** there is no server-side session store. The session (`lt_session` cookie) holds the user's Google ID (`sub`), email, name, picture, **refresh token**, and spreadsheet ID, encrypted and authenticated with Fernet using a key derived from `SESSION_SECRET`. Cookies from before phase 3 have no spreadsheet ID and are rejected, so those users sign in again.

**Access tokens:** Google access tokens last about an hour. The backend keeps each one in server memory (keyed by a hash of the refresh token) until two minutes before it expires, then refreshes it. Nothing is written to disk; after a restart each user needs one extra refresh. The access token from sign-in seeds the cache.

- Cookie flags: `HttpOnly`, `SameSite=Lax`, `Secure` outside development, 30-day lifetime (`SESSION_MAX_AGE_DAYS`). The server also rejects tokens older than that.
- `GET /api/auth/me` returns only email, name, and picture. Tokens never reach JavaScript.
- `POST /api/auth/logout` clears the cookie. It does not revoke Google access; users can do that in their Google Account.
- Changing `SESSION_SECRET` signs everyone out.
- In stage 3 the refresh token moves to the encrypted server-side token store and the cookie keeps only a session reference.

**Deployment:** the cookie is first-party, so in production the frontend must reach the API on the same site, e.g. a Vercel/Netlify rewrite from `/api/*` to the backend (set `VITE_API_BASE_URL` empty).

### Android app sign-in (bearer token)

Google refuses sign-in inside an app's WebView, and the WebView (origin `https://localhost`) can't use the backend's cookies. So the Android app signs in through the **system browser** and gets the same sealed session as a **bearer token**:

1. The app creates a PKCE verifier (kept in app storage) and opens `GET /api/auth/google?app=android&challenge=<S256 challenge>` in the system browser (Custom Tabs). The challenge is stored in the `lt_oauth` state cookie.
2. Google Sign-In runs as for the web (same redirect URI, same checks, storage setup).
3. Instead of setting a cookie, the callback redirects to `com.loonietoonie.app://auth/callback?code=<handoff>` (`APP_REDIRECT_URI`). The handoff code is the session plus the challenge, sealed with `SESSION_SECRET`, valid for **2 minutes**. Errors go to the same deep link as `?error=<reason>`.
4. Android opens the app (intent filter on that scheme/host/path). The app sends `POST /api/auth/app/token {code, verifier}`; the backend checks that SHA-256(verifier) matches the challenge and returns `{token, expires_in_days}`. An app that intercepts the deep link can't use the code without the verifier.
5. The app stores the token with Capacitor Preferences (app-private storage; Android backup and device transfer are disabled for the app) and sends `Authorization: Bearer <token>` on every API call (`credentials: 'omit'`). The backend accepts a bearer token or the cookie (`get_current_user`); both expire after `SESSION_MAX_AGE_DAYS`.
6. Signing out in the app deletes the token. CORS allows `https://localhost` (in `FRONTEND_ORIGINS` by default) with the `Authorization` header.

**Development backend for the app:** the phone (USB) reaches this computer through `adb reverse tcp:8000 tcp:8000`, so the app calls `http://localhost:8000`, and Google's redirect to `http://localhost:8000/api/auth/callback` (already registered) also works in the phone's browser. Google doesn't accept LAN IPs as redirect URIs. The network security config allows plain HTTP only to `localhost`, `127.0.0.1`, and `10.0.2.2` (emulator); everything else is HTTPS-only. A release build needs an HTTPS backend URL in `VITE_API_BASE_URL`.

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
4. When the `categories` tab is created, it is filled with the default categories (§4). An existing `categories` tab is never reseeded. Likewise, when the `subcategories` tab is created (new spreadsheets, and existing ones the first time they are migrated after the tab was added), it is filled with the default subcategories; an existing one is never reseeded.
5. Data migration: categories the app relies on (currently **Tax & Fees**) and default categories added later (`ADDED_CATEGORIES`: **Travel**, **Hobbies**, **Education**) are appended to an existing `categories` tab if no row with that name exists. A row the user soft-deleted counts as existing, so it is not re-added. Migrations of one spreadsheet run one at a time (the app sends several requests at once); extra active copies of these app-added categories and subcategories, appended when two migrations once ran together, are soft-deleted (the first copy is kept; the user's own duplicates are left alone).
6. Data migration for subcategories: defaults added after the tab first shipped (`ADDED_SUBCATEGORIES`: Groceries › Tofu & Plant Protein, Groceries › Rice, Grains & Cereal, the Travel, Hobbies, and Education subcategories, Utilities › Software & Apps, and Household › Electronics & Appliances) are appended unless a row with that category and name exists (soft-deleted counts); defaults renamed since (`RENAMED_SUBCATEGORIES`: Groceries › Produce → Fresh Fruit & Vegetables) are renamed in place on an active row when no row with the new name exists. Saved items keep the old name. Blank descriptions of default subcategories are filled with the default description, and a default description changed since (`OLD_DESCRIPTIONS`: Kids › School Supplies no longer covers school fees, which moved to Education) is replaced while the row still has the old text; descriptions the user wrote are never changed.

First sign-in costs 2 Drive writes and 2 Sheets writes. Later sign-ins cost 4 reads and no writes when everything is already current.

The migration also runs the first time each server process uses a spreadsheet (`migrate_once`, on the first API request), because sessions last for weeks: a schema change reaches existing users without asking them to sign in again.

### Scan flow

1. The user takes or picks a receipt photo on the Scan page. The browser downsizes it to at most 2000 px on the long edge (JPEG, encoded with `canvas.toDataURL`: `toBlob` takes ~4 s in the Android WebView). **Scanning starts immediately** (no extra button) and a full "Scanning your receipt…" screen appears as soon as the photo arrives and shows it with a scan line, the current stage (preparing the photo → reading the text → finding the items and prices → taking a closer look at the photo), progress, and Cancel. On failure the Scan page shows the error with Try again / Retake / Choose from gallery / Enter by hand.
2. OCR runs **on the device** through one function, `recognizeReceipt()` in `frontend/src/ocr/`. Platform differences stay inside that folder.
   - Web/PWA: **Tesseract.js** with English, French, and Japanese language data (downloaded once, then cached by the browser).
   - Android app: **Google ML Kit** text recognition v2 (bundled Latin and Japanese models, offline) in a local Capacitor plugin (`ReceiptOcr`). Both recognizers run; the Japanese result is used when it contains Japanese characters, otherwise the Latin one (better with French accents). The plugin returns lines with bounding boxes, and `frontend/src/ocr/rows.ts` rebuilds printed rows by vertical position, because ML Kit returns item names and prices as separate columns. iOS later: Apple Vision.
   - The Scan page has **Choose photo or PDF** (Android's file picker or a computer's file picker, for images and PDFs) and **Enter by hand**. The Android app also has **Take photo**; the website does not.
   - **PDF receipts** (`frontend/src/capture/pdf.ts`, pdf.js loaded only when a PDF is chosen, not in the PWA cache): up to 5 pages (more → an error asking for just the receipt's pages). Each page is rendered to a 1600 px wide JPEG and goes through the multi-page steps (stitched for review and Drive, all pages on Claude's retry). A page with embedded text (≥ 20 characters, e.g. an e-receipt) is read from that text, with lines rebuilt from the text positions, instead of by OCR; scanned PDF pages are OCR'd. Password-protected PDFs get a clear error.
   - **Take photo in the Android app** opens **Google's ML Kit Document Scanner** (local Capacitor plugin `DocumentScanner`, `play-services-mlkit-document-scanner`): it finds the receipt's edges, crops and straightens it, and cleans it up, so OCR gets a flat image without background. Its screen runs in Google Play services (Google's own buttons, which the app can't relabel), needs no camera permission, and keeps the picture on the phone. **Long receipts:** up to 5 pages per scan, photographed as overlapping parts from the top; gallery import inside the scanner is off. Backing out returns to the Scan page. On first use Play services may download the scanner module.
   - Fallback when the scanner is unavailable: the camera through a file input (`capture`), where the system camera app takes the photo. The app declares no camera permission. The manifest declares `<queries>` for `android.media.action.IMAGE_CAPTURE`: on Android 11+ the app can't otherwise see the camera app, and Capacitor silently falls back to the gallery picker. The app falls back when the scanner reports it is unavailable (e.g. no Google Play services).
   - **Several parts:** each page is downsized and read separately; `recognizeReceiptParts()` joins the texts in order with `--- part N of M ---` lines. The system prompt explains the markers and that consecutive parts usually overlap (a line repeated across a boundary is one line). For the review screen and Drive, the pages are stitched top to bottom into one JPEG (all at one width, at most 1400 px wide and 14,000 px tall).
3. The frontend sends **only the OCR text** to `POST /api/receipts/scan`.
4. The backend asks Claude (`ANTHROPIC_RECEIPT_MODEL`, default `claude-haiku-5-5`) to read the text into a draft receipt. Output is **strict JSON** via structured outputs (`output_config.format` with a JSON schema whose `category` is an enum of the user's categories).
5. The backend normalizes and validates the draft (§5). Nothing is saved.
   - If a blocking issue is found and no photo was sent, the response has `needs_image: true`. The frontend then sends the same request **once** more with the photo (base64) — or, for a long receipt, all its parts in order (`images`, at most 5, 15 MB together) — and Claude reads the photos plus the text.
   - The photo leaves the device only for that retry, and never on a clean first reading. A second failure does not retry again; the issues are shown on the review screen.
   - Empty OCR text skips the first Claude call and asks for the photo directly.
6. The user reviews and edits the draft on the review screen (the receipt form, pre-filled, with the issues listed and the photo shown), then saves with `POST /api/receipts`.
7. After saving, the frontend uploads the photo with `PUT /api/receipts/{id}/image`. The backend stores it in Drive under `LoonieToonie/receipts/` (created on first use, found by the app property `loonietoonie=receipts-folder`) as `YYYY-MM-DD <store> <id prefix>.jpg`, and writes its link to `image_link`. If the upload fails, the receipt stays saved and the user is told.

## 4. Data model (spreadsheet tabs)

| Tab | Columns |
| --- | --- |
| `receipts` | receipt_id, store, purchased_at, total, currency, image_link, created_at, updated_at, deleted |
| `items` | item_id, receipt_id, name, quantity, price, category, subcategory, updated_at, deleted |
| `categories` | name, icon, updated_at, deleted |
| `subcategories` | category, name, description, updated_at, deleted |
| `bank_accounts` | account_id, item_id, institution, name, mask, type, subtype, currency, import_format, updated_at, deleted |
| `bank_transactions` | transaction_id, account_id, date, merchant, description, amount, currency, kind, category, subcategory, plaid_category, pending, receipt_id, edited, import_id, import_key, updated_at, deleted |
| `imports` | import_id, account_id, filename, format (`csv`/`ofx`), date_from, date_to, added, duplicates, skipped, created_at, updated_at, deleted |
| `merchant_categories` | merchant (merchant key), kind, category, subcategory, updated_at, deleted |
| `merchant_names` | merchant (bank key: the merchant key of the charge's own cleaned bank text), name, updated_at, deleted |
| `chat_messages` | message_id, conversation_id, role (user/assistant), content (≤ 45,000 characters), created_at, updated_at, deleted |
| `settings` | key, value, updated_at (keys: `household_type`, `province`, for Compared with similar households) |
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

Created with a new `categories` tab. Users can edit them later. Categories are listed in the tab's order, except that **Tax & Fees** and the catch-all **Other** are always listed last, in that order (in the app and in Claude's list), even when categories were added after them.

| Icon | Name | Icon | Name |
| --- | --- | --- | --- |
| 🛒 | Groceries | 💊 | Health |
| 🍜 | Dining Out | 🧴 | Personal Care |
| 🧻 | Household | 👕 | Clothing |
| 🚗 | Transportation | 🎮 | Entertainment |
| 💡 | Utilities | 🧸 | Kids |
| 🏠 | Housing | 🐾 | Pets |
| ✈️ | Travel | 🎨 | Hobbies |
| 🎓 | Education | 🧾 | Tax & Fees |
| 📦 | Other | | |

**Tax & Fees** holds taxes and fees added on top of item prices (GST/HST/PST, TPS/TVQ, US sales tax, Japanese 外税, deposits, eco and bag fees, tips). The app relies on it, so it is added to existing spreadsheets on sign-in (§3).

### Subcategories

Optional finer groups within a category (Groceries › Bakery). One row per subcategory in the `subcategories` tab, linked to its category by name (case-insensitive). Users add, rename, describe, or soft-delete them in the spreadsheet; rows for a category that doesn't exist are ignored. `description` says what a subcategory covers (e.g. Pantry: "Shelf-stable cooking basics: oils, vinegar, sauces, …"); Claude follows it when scanning, and the Account page shows it. Every default subcategory comes with a description (`SUBCATEGORY_DESCRIPTIONS` in `app/sheets/schema.py`). An item's `subcategory` is blank when it has none. Defaults (written when the tab is created):

| Category | Subcategories |
| --- | --- |
| Groceries | Fresh Fruit & Vegetables; Meat & Seafood; Tofu & Plant Protein; Dairy & Eggs; Bakery; Pantry; Rice, Grains & Cereal; Frozen; Snacks & Sweets; Drinks |
| Dining Out | Restaurants, Fast Food, Coffee & Cafés, Delivery, Bars |
| Household | Cleaning, Paper Goods, Kitchen, Furniture & Decor, Tools & Hardware, Electronics & Appliances (computers, phones, TVs, appliances, and their repairs) |
| Transportation | Fuel, Public Transit, Taxi & Rideshare, Parking, Car Maintenance |
| Utilities | Electricity, Gas & Heating, Water, Internet, Phone, Software & Apps (software, AI, cloud storage, and app subscriptions; streaming stays in Entertainment › Subscriptions) |
| Housing | Rent & Mortgage, Repairs, Insurance |
| Health | Pharmacy, Vitamins & Supplements, Medical & Dental, Vision |
| Personal Care | Hair, Skin Care, Cosmetics, Hygiene |
| Clothing | Clothes, Shoes, Accessories |
| Entertainment | Movies & Shows, Games, Books & Music, Events, Subscriptions |
| Kids | Toys, School Supplies, Baby Care, Activities |
| Pets | Pet Food, Vet, Pet Supplies |
| Travel | Flights; Lodging; Car Rental; Trains & Buses; Tours & Activities; Travel Insurance & Fees |
| Hobbies | Arts & Crafts; Sports & Fitness; Outdoors & Camping; Music & Instruments; Gardening & Plants; Classes & Clubs (things you do or make; Entertainment is what you watch, read, or listen to) |
| Education | Tuition & Courses; Books & Materials; Tutoring; Online Learning & Apps; Exams & Certifications (learning for school, work, or a skill, for adults or children; leisure classes stay in Hobbies › Classes & Clubs, children's school supplies in Kids) |
| Tax & Fees | Sales Tax, Deposits & Eco Fees, Bag Fees, Tips & Service |
| Other | (none) |

## 5. Receipt parsing and validation

### Saving a receipt (manual entry and confirmed scans)

`POST /api/receipts` and `PUT /api/receipts/{id}` accept the same body and enforce:

- `store`: 1–200 characters. `purchased_at`: a real date, at most 1 day in the future (time zones).
- `currency`: a 3-letter code, upper-cased. Defaults to `DEFAULT_CURRENCY` (`CAD`) when omitted.
- `total`: 0 or more. Money values allow at most 3 decimal places; no NaN or infinity.
- `items`: 1–200 items. `price` is the **unit price**; the line amount is `price × quantity`. `quantity` is greater than 0 (default 1). A negative `price` is allowed for discount lines.
- Each item's `category` must exist in the user's `categories` tab. It is matched case-insensitively and saved with the tab's spelling.
- An item's `subcategory` is optional (blank or missing = none). When given, it must be one of that category's subcategories; it is matched case-insensitively and saved with the tab's spelling. On edit, an existing item may keep the subcategory it already has even if that name was renamed or removed in the sheet since (so renaming a subcategory never blocks editing old receipts).
- On edit, an `item_id` must belong to that receipt and appear once. Items without `item_id` are added; existing items left out are soft-deleted.
- The total is what the user enters; it is **not** required to equal the items' sum (tax, fees). The form pre-fills it with the items' sum and shows any difference.
- **Duplicates** (`app/services/duplicates.py`): a new receipt is a likely duplicate of a saved one when the purchase date, total, and currency are the same and the store names match (shared words ignoring case, accents, and store numbers, or, for names without Latin words such as Japanese, the same name without spaces and punctuation). `POST /api/receipts` then answers **409** with `detail.duplicate` (the saved receipt's ID, store, date, total, currency) instead of saving; the form shows "Already saved?" with **View saved receipt** and **Save anyway**, which repeats the request with `allow_duplicate=true`. Edits are not checked.

Validation errors return 422 with FastAPI's `detail` list (`loc`, `msg`), which the frontend shows per field.

### Claude parsing (scan)

Claude's answer (schema in `app/ai/receipt_parser.py`): `store`, `purchased_at` (`YYYY-MM-DD` or null), `currency` (ISO 4217 or null), `total`, and `items` of `name`, `quantity`, `unit_price`, `category`, `subcategory` (or null). Money values are decimal **strings**, converted to `Decimal` by the backend.

How receipt lines map to items (in the prompt):

- `unit_price` is the price of one unit: "2 @ 1.99 3.98" is quantity 2, unit price 1.99. Weighed items are quantity 1 at the line amount, with the weight in the name.
- **Discounts**, coupons, and points redeemed are items with a negative price, in the category of the product they discount.
- **Taxes and fees added on top** of item prices are items in the **Tax & Fees** category (or Other if the user removed it). So category totals add up exactly to receipt totals.
- **Tax already included** in prices (Japanese 内税, VAT summaries) is informational and is not an item.
- `total` is the amount paid including tax; subtotals, tendered cash, change, and card lines are ignored.
- The prompt lists each category with its subcategories, one per line with its description when it has one. Claude picks a subcategory of the item's category, or null when none fits. The schema can't tie subcategories to categories, so the backend drops any subcategory that isn't in the final category's list.
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
| A saved receipt has the same store, date, total, and currency | `possible_duplicate` | no |

Claude errors, refusals, and cut-off answers return 502 ("enter it by hand"); a missing `ANTHROPIC_API_KEY` returns 503.

Tests: `tests/samples/` holds OCR texts in English (Ontario, HST), French (Québec, TPS/TVQ), Japanese (tax included, and tax added with a 令和 date), and US English (sales tax and tip), plus real Tesseract.js output for three of them. Unit tests use a fake Claude; `RUN_LIVE_CLAUDE=1 pytest tests/live` sends the samples to the real API (well under one cent per run).

## 6. API

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Health check (phase 1) |
| GET | `/api/auth/google`, `/api/auth/callback` | Google Sign-In (§3) |
| GET | `/api/auth/me` | Signed-in user (email, name, picture, `spreadsheet_url`); 401 if not signed in |
| GET | `/api/categories` | Active categories (`name`, `icon`, `subcategories`, `descriptions`: subcategory → description) from the `categories` and `subcategories` tabs |
| POST | `/api/auth/logout` | Clear the session cookie |
| POST | `/api/receipts/scan` | `{ocr_text, image?, image_media_type?}` or `{ocr_text, images?: [{data, media_type}]}` (parts of a long receipt, in order, ≤ 5) → Claude → `{draft, issues, needs_image, used_image}`. Nothing is saved (§3, §5) |
| PUT | `/api/receipts/{id}/image` | Body: the photo bytes (`image/jpeg`, `png`, or `webp`, ≤ 5 MB). Saves it to Drive and sets `image_link` |
| POST | `/api/receipts` | Save a receipt + items (§5); returns 201 with the saved receipt, or 409 for a likely duplicate unless `allow_duplicate=true` |
| GET | `/api/receipts` | Active receipts with their items, newest purchase first. Filters: `from`, `to` (`YYYY-MM-DD`, inclusive), `category`, `subcategory` (receipts with at least one matching item) |
| GET | `/api/receipts/{id}` | One receipt with its items; 404 if missing or deleted |
| PUT | `/api/receipts/{id}` | Replace a receipt's fields and items (§5) |
| DELETE | `/api/receipts/{id}` | Soft-delete the receipt and its items; 204 |
| GET | `/api/summary?month=YYYY-MM&currency=CAD` | Spending patterns for one month and one currency (§7 Dashboard). Defaults: this month, `DEFAULT_CURRENCY` |
| GET | `/api/household` | The household type and province (`""` when not set) with the choices (`household_types`, `provinces`: `{value, label}`) |
| PUT | `/api/household` | `{household_type, province}` → saved in the `settings` tab; 422 for values not in the choices |
| DELETE | `/api/household` | Clears the household type and province (Account → Your household → **Clear**, after a confirmation); Patterns stops comparing until they're set again |
| GET | `/api/chat/conversations` | Saved Ask conversations, newest first: `conversation_id`, `title` (first question, ≤ 80 characters), `updated_at`, `message_count` |
| GET | `/api/chat/conversations/{id}` | A saved conversation's messages `{role, content, created_at}` in order; 404 if none |
| DELETE | `/api/chat/conversations/{id}` | Soft-deletes a conversation's messages; 404 if none |
| GET | `/api/household/comparison` | Compared with similar households (§7 Dashboard): `status` (`ok`, `not_set`, `no_history`), household, region, `start` and `end` (January 1 or the first purchase this year, to today), survey and price years, rows `{category, yours, typical, difference}` |
| POST | `/api/chat` | Spending chatbot. Body: `{messages: [{role, content}], conversation_id?}` (text only, last one from the user, ≤ 40 messages, ≤ 4000 chars each). Streams newline-delimited JSON events (`conversation` first, then `text`, `status`, `reset`, `error`, `done`); a completed answer is saved with its question in `chat_messages` (§7 Chat) |
| GET | `/api/bank/items` | `{enabled, items}`: linked banks/cards with their accounts and last sync (`enabled: false` when Plaid isn't configured) |
| POST | `/api/bank/link-token` | `{link_token}` to open Plaid Link (503 if not configured) |
| POST | `/api/bank/items` | `{public_token}` from Plaid Link → store the encrypted token, save accounts, first import; 201 with the sync result |
| DELETE | `/api/bank/items/{item_id}` | Unlink: revoke at Plaid (`/item/remove`), delete the token, soft-delete the accounts. Imported transactions stay unless `remove_transactions=true`; 204 |
| POST | `/api/bank/sync` | Import new/changed/removed transactions from every linked item → per item `{status: ok / needs_relink / error, changes, bank_updated_at, refresh_requested}`. `refresh=true` also asks Plaid to read each bank now (at most hourly) |
| GET | `/api/bank/transactions` | Imported transactions, newest first. Filters: `from`, `to`, `account_id`, `category` |
| GET | `/api/bank/transactions/{id}/candidates` | Receipts that could explain a charge (same currency, ±7 days), best first, with `likely` |
| PUT | `/api/bank/transactions/{id}/category` | `{kind, category, subcategory, apply_to_merchant}` → `{changed}`: set a charge's category (or not spending), with `apply_to_merchant` for every charge from the merchant now and at import |
| PUT | `/api/bank/transactions/{id}/receipt` | `{receipt_id}` to match by hand, or `{receipt_id: null}` for "no receipt"; 204 |
| GET | `/api/accounts` | Bank and card accounts (linked and file-import accounts) with their last import (`date_to`, `created_at`) |
| POST | `/api/accounts` | `{institution, name, type: depository/credit, mask?, currency}` → a file-import account; 201 |
| PUT | `/api/accounts/{id}` | Same body: change a file-import account (409 for a Plaid-linked one) |
| DELETE | `/api/accounts/{id}` | Soft-delete a file-import account; its transactions stay unless `remove_transactions=true`; 204 |
| POST | `/api/imports/preview` | `{account_id, filename, data, mapping?}`: `data` is the file (CSV, OFX, or QFX, ≤ 2 MB, ≤ 5,000 rows) in base64, `mapping` the columns chosen in the preview. → `{format, columns, examples, mapping, mapping_confident, rows: [{line, date, description, amount, status: new/duplicate/possible_duplicate/skipped, reason}], counts, date_from, date_to}`. Nothing is saved. 409 for a Plaid-linked account, 413 for a file too large, 422 for one that can't be read |
| POST | `/api/imports` | Same fields plus `include_lines` (possible duplicates the user ticked) → parse again, dedupe, categorize, save; 201 `{import_id, added, duplicates, skipped}` |
| GET | `/api/imports?account_id=` | Past imports, newest first |
| DELETE | `/api/imports/{id}` | Undo an import: soft-delete its transactions and the `imports` row; 204 |
| GET | `/api/export/csv` | CSV download (§7 Export). `kind=items` (default) or `receipts`; filters `from`, `to`, `category`, `subcategory`, `currency` |

Request and response bodies are Pydantic models. Money values (`total`, `price`, `quantity`) are JSON **strings** in responses (e.g. `"3.49"`); requests accept strings or numbers. Storage problems return 502; an expired Google grant returns 401.

Receipt response:

```json
{
  "receipt_id": "uuid", "store": "FreshMart", "purchased_at": "2026-10-01",
  "total": "7.47", "currency": "CAD", "image_link": "",
  "created_at": "2026-10-13T09:30:00Z", "updated_at": "2026-10-13T09:30:00Z",
  "items": [{"item_id": "uuid", "name": "Milk", "quantity": "1", "price": "3.49", "category": "Groceries", "subcategory": "Dairy & Eggs"}]
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

- Pages: Login, Patterns (home), Transactions, Scan (capture → review/edit → save), Ask (chatbot), Account.
- **Navigation:** phones get a bottom tab bar (Patterns · Transactions · **Scan** as a raised centre button · Ask · Account) that hides while typing; wide screens (≥ 900 px) get the same links in the top bar. The top bar shows the logo and the user's avatar (→ Account). Layout respects the Android safe areas (edge-to-edge).
- **Look:** light theme, Plus Jakarta Sans (self-hosted via `@fontsource-variable`, no font requests to Google), soft cards, pink accent `#d63a6f` (passes AA with white text), loon-eye-and-coin logo (`public/loonietoonie03.png`, a yellow `#F3BA5B` rounded tile; the in-app `loon-badge.png`, favicon, PWA and Android icons and the splash screen are generated from it), inline SVG icons (`src/components/Icon.tsx`), skeleton loading, motion off under `prefers-reduced-motion`.
- Built so far (routes via React Router):
  - **Login** (no route of its own): anyone who isn't signed in sees it on every route. Mascot, three feature lines, "Continue with Google", and the privacy note (receipts stay in the user's own Drive; "By continuing, you agree to the Privacy policy", linking `docs/privacy.html` on GitHub Pages, `PRIVACY_POLICY_URL` in `src/lib/links.ts`). The Account page links the same policy below the spreadsheet link. On wide screens (≥ 900 px) it splits into two columns: the pitch on the left, a "Get started" sign-in card on the right. Sign-in errors show here. While the session is checked a splash shows; if the server can't be reached, a "Try again" button.
  - `/` **Patterns** (the home page): the dashboard (see below), with a greeting. `/dashboard` redirects here.
  - `/account`: profile, spreadsheet link, Your household, **Accounts & cards** (accounts filled from statement files, phase 11), **Linked banks (Plaid)** (linked banks/cards with their accounts, Sync now, Unlink, Link a bank or credit card; hidden when Plaid isn't configured), categories, Sign out.
  - `/import`: import a statement file (§7 Statement import).
  - `/scan`: take or choose a photo; scanning starts at once on a "Scanning your receipt…" screen (on-device OCR, then the text, and the photo once if needed), then the review screen opens.
  - `/transactions`: **all** receipts by default (each card collapsed to store, date, item count and total; tap to see the items, Edit and Photo), newest purchase date first, grouped under month headings with each month's total (per currency). Optional filters as chips (All, This month, a month picker, category, subcategory), kept in the URL. After saving, the list opens unfiltered and scrolls to and highlights the saved receipt, so receipts dated in other months are never hidden.
  - `/receipts/new` and `/receipts/:id/edit`: the receipt form (store, date, currency, items with quantity, unit price, category and an optional subcategory picked from that category's list, total). The total follows the items' sum until the user edits it. Edit also offers Delete. The currency is a dropdown of common currencies (CAD, USD, JPY, EUR, GBP, AUD, CNY, INR, KRW, MXN) with **Other…** for any 3-letter code, and defaults to `DEFAULT_CURRENCY` (from `/api/auth/me`).
  - Review screen: `/receipts/new` with a scan result in router state. It is pre-filled from the draft, lists the issues, shows the photo, keeps the scanned total as read, and uploads the photo after saving.
  - Item rows show the subcategory (or the category when there is none). Choosing a category adds a subcategory filter chip when that category has subcategories (`subcategory` in the URL).
  - The Account page lists the categories; tapping one shows its subcategories with their descriptions. It says they are edited in the spreadsheet.
  - Transactions show a Photo link for receipts with a photo, and "Items CSV" / "Receipts CSV" buttons for the current filters.
- Money in the frontend is handled as decimal strings and bigint math (`src/lib/decimal.ts`), never JS floats.

### Bank and card linking (phase 10a)

Purpose: capture spending that has no receipt photo (card and bank transactions), through **Plaid** (Canada and US).

- **Linking:** Account → "Bank accounts & cards" → **Link a bank or credit card** opens **Plaid Link** (`react-plaid-link`) with a link token from `POST /api/bank/link-token` (products `transactions`, up to 730 days of history). The bank sign-in happens inside Plaid; LoonieToonie never sees bank passwords. Plaid Link returns a public token, which `POST /api/bank/items` exchanges for an access token, stored **only** in the encrypted token store (§8), never in the Sheet or the browser.
- **Import:** `POST /api/bank/sync` uses Plaid `/transactions/sync` from the saved cursor, page by page (restarting from the saved cursor on `TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION`). Accounts are refreshed into `bank_accounts`; added and modified transactions are upserted into `bank_transactions` by `transaction_id`, removed ones soft-deleted. On update, the bank's fields change but `kind`, `category`, `subcategory`, `receipt_id`, and `edited` (the user may have edited them in the Sheet) are kept. Plaid often improves a charge's category after the first import (a pending charge's category is often vague), so a row the user never chose a category for follows it: when the row isn't `edited` and still shows what an import gives (its remembered merchant category, Plaid's mapped category, or Other), it takes the new import's category. `edited` is TRUE once the user chooses a kind/category/subcategory in the app (Change category); choices typed into the Sheet are kept because they differ from what an import gives. The cursor is saved **only after** the Sheet write succeeds, so a failed sync is repeated safely. One sync at a time per user.
- **When it syncs:** on demand only (no background jobs or webhooks): when linking, the first time the "Bank & card" view opens in an app session, and with the **Sync** button (top of the Bank & card view, and **Sync now** on Account).
- **Asking the bank now:** Plaid reads banks on its own schedule (it can be a day). The Sync button calls `POST /api/bank/sync?refresh=true`, which also asks Plaid to read each bank right away (`/transactions/refresh`, at most once an hour per bank; free on the Trial plan, billed per request on paid plans). Plaid reads the bank in the background, so the app checks back up to 4 times, 8 seconds apart, for the new transactions. When nothing new arrives, the app says when Plaid last read each bank (`bank_updated_at` from Plaid's item status) instead of "up to date"; the Bank & card view shows that time above the list.
- **Dates:** a transaction's date is the **purchase date** (Plaid `authorized_date`), as bank and card websites show it; the posting date (Plaid `date`, up to several days later on credit cards) is used only when the purchase date is missing. Items imported before the current import rules (`data_version` below 6 in the token store: 2 = purchase dates, 3 = merchant names, 6 = generic words skipped in the merchant-name check, 7 = two-way agreement, 8 = Plaid's VERY_HIGH matches) are re-imported once from the start; later versions only move charges whose Plaid category got a new mapping and that still show the old result (exactly the old category and subcategory, no remembered category for the merchant), without re-importing: 4 = travel from Transportation to Travel, 5 = gyms (from Personal Care) and sporting goods (from Other) to Hobbies › Sports & Fitness, 9 = education from Kids › School Supplies to Education › Tuition & Courses, 10 = electronics stores from Other to Household › Electronics & Appliances (`CATEGORY_MOVES`). A re-import corrects rows in place by `transaction_id` (user edits to kind, category, subcategory, and matches are kept).
- **Amounts:** Plaid's sign is kept: positive = money spent, negative = refund or money in.
- **Merchant names** (`app/services/names.py`): `description` keeps the bank's own text; `merchant` is the name shown. Plaid's merchant name is a guess from its business database and is sometimes wrong ("SUZUYA JAPANESE MARKET" → "Suzuyo America Inc."), so it is used only when it **agrees with the bank's text both ways**: every distinctive word of Plaid's name appears in the bank's text (a bank word cut short counts, "BATH BODY WOR" for Bath & Body Works; so not "Triple O Construction" for `TRIPLE O # 570 KINGSWAY`), and the bank text's first distinctive word appears in Plaid's name (not "Whistler's Inn" for `EARLS WHISTLER`). Generic words such as city, of, the, cafe, restaurant, market, or hotel don't count either way (not "City of Vancouver" for `CITY BBY …`, the City of Burnaby), and letters joined by "&" are one word (A&W); Plaid's own confidence in the match (the merchant counterparty's `confidence_level`) is taken into account: all the wrong guesses found were rated HIGH, so HIGH and lower get the full two-way check, while **VERY_HIGH** matches (known chains, which banks often abbreviate: `REAL CDN SUPERS`, `KELOWNA ESSO`) need only one shared distinctive word in either direction — still not none ("New York Life" for `NYF #3304`, New York Fries, is rejected); otherwise the bank's text is cleaned up and shown: purchase prefixes ("RETAIL PURCHASE", "POS", "INTL VISA DEB"), reference, order, card, and phone numbers, and foreign-amount tails ("13.32 USD @ 1.42") are removed, capitalized words get title case, and a trailing province code stays ("Suzuya Japanese Market Burnaby, BC").
- **Remembered categories:** each bank row has **Change category** (a category and subcategory, or "Not spending" as a transfer or income) with **All charges from <merchant>, including new ones** ticked by default (`PUT /api/bank/transactions/{id}/category`). With it, the choice is saved in `merchant_categories` under the merchant key (the name's first two meaningful words, lower-case and accent-free, so "Suzuya Japanese Market Burnaby, BC" and "Suzuya Japanese" are one merchant), applied to that merchant's existing charges at once, and used instead of Plaid's category for its new charges at import. The shown merchant name can change after a rule is saved (learned names, better name cleanup), so at import a rule is also found by the merchant key of the charge's own bank text and of Plaid's merchant name. Once (items synced before `data_version` 13), existing rows are settled: a row showing Other or Plaid's mapped category while its remembered category (found this way) or Plaid's mapped category is something else gets that category, and a row with any other category is marked `edited`.
- **Names learned from receipts:** when a charge is matched to a receipt whose store name shares a word with the charge, the receipt's store name is saved in `merchant_names` under the charge's bank key (the merchant key of its own cleaned bank text, not of Plaid's guess, so it is the same on every import; the latest receipt wins). It is shown at once on all that merchant's charges and used instead of Plaid's or the bank's name for new charges at import. Matches by amount only (e.g. a Japanese store name against an English bank text) teach nothing. A remembered category still applies under the new name or the old one.
- **Kind and category:** Plaid's personal finance category is mapped (`app/bank/categories.py`): transfers, credit-card and other loan payments → `transfer`, income → `income` (neither counts as spending; the mortgage payment counts as Housing › Rent & Mortgage); everything else → `spending` with the user's category and subcategory (e.g. `FOOD_AND_DRINK_COFFEE` → Dining Out › Coffee & Cafés, `TRAVEL_FLIGHTS` → Travel › Flights, gyms and sporting goods → Hobbies › Sports & Fitness, `GENERAL_SERVICES_EDUCATION` → Education › Tuition & Courses, `GENERAL_MERCHANDISE_ELECTRONICS` → Household › Electronics & Appliances). Names the user doesn't have fall back to Other / no subcategory. The raw Plaid code is kept in `plaid_category`.
- **Errors:** an item whose bank needs a new sign-in (`ITEM_LOGIN_REQUIRED` and similar) reports `needs_relink` and is marked `needs_reconnect` in the token store (cleared by the next successful sync). Other Plaid errors report `error` for that item only.
- **Linking another bank** when one is already linked first asks for confirmation and points to Reconnect, since linking the same bank again creates a second link (and uses one of the plan's Plaid links).
- **Reconnect** (Plaid update mode): the Account page shows **Reconnect** on an item marked `needs_reconnect`. `POST /api/bank/link-token` with `{"item_id": ...}` creates a Link token with the item's access token and no products; the user signs in to the bank again in Plaid Link, no token is exchanged, and the app syncs. The item, its account and transaction IDs, and its sync cursor stay, so only new transactions come in and nothing is imported twice. Linking the bank again instead creates a new item, which the relink merge below cleans up.
- **Unlink:** revokes the item at Plaid, deletes its token, and soft-deletes its accounts. Imported transactions stay in the Sheet.
- **Linking a bank again** (`app/services/relink.py`): Plaid gives a new link new account and transaction IDs, so its first import would repeat every transaction. After linking and after each sync, an account of the newest link replaces any older or unlinked account with the same institution, type, and last 4 digits. Old transactions are paired with the new ones (same amount, currency, and bank text, dates at most 3 days apart); the new row takes the user's kind, category, subcategory, and receipt match, and the old row is soft-deleted. Old rows without a pair move to the new account (history the new link doesn't return), except pending ones, which never post and are soft-deleted. An older link that is still linked and whose accounts were all replaced is unlinked (revoked at Plaid, token deleted). Accounts with other last 4 digits (e.g. a partner's login at the same bank) are left alone. The user's choices also come back from the old accounts' soft-deleted rows (an earlier merge, or unlinking with "remove transactions" before linking again): a deleted copy paired with a new row passes its kind, category, and subcategory when they are the user's choice (`edited`, or for rows from before that column, different from Plaid's mapped category and not Other: a row that kept Other after Plaid improved its category isn't a choice), and its receipt match when the receipt isn't matched elsewhere. When several old links have a copy, the newest link's wins. This applies to rows just imported, and once to all rows of items synced before `data_version` 13, never to an `edited` row.
- **Transactions page:** a **Receipts / Bank & card** switch (`view=bank` in the URL) appears once a bank is linked. The bank view starts with when each bank was last read and a **Sync** button, then shows transactions by month with merchant, date, account (name •• last 4), category or Transfer/Income, Pending, and refunds as +amount. Its total counts only `spending`. Month and category filters apply.
- **Unlink and remove:** Unlink also asks whether to remove the bank's imported transactions (soft-deleted; `DELETE …?remove_transactions=true`), e.g. to clear Sandbox test data.

### Plaid on the web only, for invited households (phase 12)

Purpose: publish the Android app (and offer the web app) with **statement import only**, while the developer's own household keeps Plaid sync on the web app. Google Play reviews the Android app as shipped, so Plaid must not be in the Android build at all, not merely hidden: an app that turns on a feature after review breaks Play policy. Plaid's Trial (10 linked items) covers the household. **Later (decided 2026-10-23):** once Plaid grants production access, bank sync opens to every household on the web app as a **paid** feature (Plaid's per-account fees grow with each household, so it has to pay for itself); the Android app stays statement-only. **Planned pricing (2026-10-28, not final):** the Plus subscription includes **one** synced account and extra accounts are paid add-ons (up to 4 in total), so each account brings in about what Plaid charges for it; numbers are settled after the closed test and Plaid's production quote. The allowlist check is the one place that decides who gets Plaid, so it becomes "allowlisted or paid" then (with a per-user account limit), together with the monetization work.

- **Who gets Plaid:** a signed-in user on the **web app** whose Google email is in `PLAID_ALLOWED_EMAILS` (a JSON list, compared case-insensitively), when the Plaid keys and `TOKEN_ENCRYPTION_KEY` are set. An empty list means nobody. Everyone else, and every request from the Android app (bearer-token sessions, §3), sees bank linking as off: `GET /api/bank/items` returns `enabled: false`, and `POST /api/bank/link-token`, `POST /api/bank/items`, and `POST /api/bank/sync` return 403. `DELETE /api/bank/items/{id}` (unlink) stays open to the item's owner from anywhere, so server-side data can always be deleted.
- **Not in the Android build:** the Plaid UI (`BankLinks` with `PlaidLinkOpener` and `react-plaid-link`: the "Linked banks (Plaid)" section on Account; `PlaidSync`: the Sync button, "last read" line, and the first sync of each session in the Bank & card view; `bankSync.ts`) loads only through lazy imports behind a build-time constant (`import.meta.env.MODE !== 'android'`), so Vite leaves it out of `vite build --mode android`; the Android app doesn't ask for `/api/bank/items` either. `npm run android:sync` (and so `android:build` / `android:run`) fails if the Android bundle contains `cdn.plaid.com`, `react-plaid-link`, `PlaidLinkOpener`, or "Linked banks (Plaid)" (`frontend/scripts/check-android-bundle.mjs`); the bundle has no mention of Plaid at all. The Android app still shows charges that Plaid synced on the web (they are rows in the user's spreadsheet), like any other transaction.
- **Duplicates both ways** (the household uses Plaid and statements for the same cards):
  - *Import after Plaid:* unchanged from phase 11: a statement row that looks like a Plaid charge of the same card is a possible duplicate, left out unless ticked.
  - *Plaid after import* (new, `merge_plaid_with_imports` in `app/services/imports.py`): after every link and sync, a **posted** Plaid charge the sync just added that looks like an imported statement row of the same card (the look-alike rules and same-card rules of §7 Statement import; each row used once) replaces it: the statement row is soft-deleted, and the Plaid row takes its kind, category, and subcategory when they were the user's choice (`edited`), its receipt match when the Plaid row has none, and its `import_key` (kept on Plaid updates), so it is never paired again and importing that statement again finds the row already imported. The Plaid row is kept because Plaid keeps updating it; a statement row never changes. Pending Plaid charges are left alone until they post (their amount can still change, e.g. a tip).
  - Once, for items synced before `data_version` 14, the same merge runs over all rows; again for items before 15, when the window between a statement row and a Plaid charge became 7 days (a Booking.com hotel charge was Oct 9 in Plaid and Oct 13 in the CIBC file). It replaces phase 11's one-time cleanup (**Remove duplicates**, `/api/imports/plaid-duplicates`, `CROSS_ACCOUNT_CHECK_SINCE`), which is removed.
  - Known gap: a pending Plaid charge and its posted statement row with a different amount both count until the Plaid charge posts.
  - The relink merge (§7 Bank and card linking) only ever pairs Plaid accounts; before phase 12 an account added for statements with the same bank, type, and last 4 digits as a Plaid card could be taken for an older link of it.
- **Privacy and Play:** the Android app's store listing and Data safety form describe statement import only (no Plaid). The privacy policy covers the whole service and keeps the Plaid section, marked "web app, invited households only". The Android app never holds bank tokens; the server keeps them only for allowlisted users (storage stage 3).
- **Tests:** allowlist (case-insensitive) and app-session checks on the bank endpoints, unlink still allowed for others, the Plaid-after-import merge (choices and receipt matches carried over, pending skipped, never paired twice, the one-time run, and through a real sync); the Android bundle check runs with every Android build.

### Receipt matching and combined spending (phase 10b)

A scanned receipt and its card charge are the same purchase, so they must count once.

- **Matching** (`app/services/matching.py`): a spending charge and a receipt match when the currency is the same, the charge is dated from 1 day before to 5 days after the purchase, and either the amount equals the receipt total (within 2 of the currency's smallest unit) or, for receipts with Dining Out items, the charge is up to 30% more (a tip on the card) **and** the store names match. Names match when they share a word after lower-casing, removing accents, numbers, and noise words ("FreshCo" ↔ "FRESHCO #1234 TORONTO"); different scripts (e.g. Japanese store names) never match by name. An amount match without a name match is accepted only when it is the only option for both the charge and the receipt. Pairs are taken best first (exact amount, name, closest date), one receipt per charge.
- **Stored** in `bank_transactions.receipt_id`: a receipt UUID (matched), empty (not matched yet), or `none` (the user said no receipt matches; never auto-matched again).
- **When:** after every bank sync or link, and after a receipt is saved, edited, or deleted (best effort). Matches to deleted receipts are cleared; other existing matches, including ones chosen by hand, are kept.
- **Fixing:** each bank row opens to show the match: **View receipt** and **Not this receipt**, or **Match a receipt** (receipts in the same currency within 7 days, best first, likely ones marked; `GET /api/bank/transactions/{id}/candidates`) and **No receipt** (`PUT /api/bank/transactions/{id}/receipt` with a receipt ID or `null`). Matching a receipt chosen by hand unmatches any other charge it was matched to. Receipt cards in the Receipts view show the card they were paid with ("💳 Plaid Credit Card •• 3333").
- **Combined spending** (`app/services/spending_sources.py`): Patterns, Ask, and CSV export use receipts **plus** every spending charge that no existing receipt explains, as a one-item record (`receipt_id` `bank:<transaction_id>`, store and item = merchant, with the charge's category and subcategory). Matched charges are left out (the receipt and its items count instead). A matched receipt counts at what the card was charged: when its matched charges (same currency, at any date) add up to more or less than the receipt, e.g. a tip added on the card, an extra item "Charged on the card (e.g. tip)" (item ID `charged:<receipt_id>`) holds the difference, in the category and subcategory with the most spending on the receipt (Tax & Fees only when nothing else). It is not an "item bought". Transfers, card payments, and income never count. Refunds (negative amounts) lower their category. Category filters apply to charges by their category.
  - Patterns: charges count in totals, categories, the trend, and top stores, but not in "Bought most often". The period line says "includes N card & bank charges without a receipt" (`bank_transaction_count`).
  - Ask: the prompt explains these records; `find_purchases` marks them `"source": "bank"`. Tool results call every purchase a "receipt", so the prompt tells Claude to say purchases or transactions, and to say answers come from receipts and bank and card transactions.
  - CSV: both exports have a `source` column (`receipt` or `bank`).
- **Android:** Plaid Link runs inside the app's WebView. Sandbox and most banks work; banks that sign in through their own app or site (OAuth) need a registered redirect URI and may need Plaid's Android SDK later.

### Statement import (phase 11)

Purpose: bring in card and bank spending **without Plaid**. The user downloads a statement file from their bank's website (CSV, or OFX/QFX, which most Canadian banks offer as "Quicken" / "Microsoft Money") and imports it. No bank sign-in, no tokens on the server; this is what lets LoonieToonie be published on Google Play without bank linking. From phase 12, statement import is the only way in the Android app and for most users; Plaid stays on the web app for invited households. Code: `backend/app/imports/` (parser, keyword rules, Claude categorizer), `app/services/imports.py`, `app/api/imports.py`; `frontend/src/pages/ImportPage.tsx`, `components/StatementAccounts.tsx`, `components/AccountForm.tsx`.

- **Accounts:** Account → **Accounts & cards** lists the accounts filled from statement files, each with what was last imported ("up to Oct 20"), **Edit** and **Import**, and under "Import history" its past imports. The edit form (also on the Import page, **Edit this account**) has **Remove account** (soft delete; a second question offers to remove its transactions too). **Add an account or card** asks for the bank (free text), an account name, type (credit card / bank account → `credit` / `depository`), the last 4 digits (optional), and the currency (default `DEFAULT_CURRENCY`). These accounts have an empty `item_id`. Plaid-linked accounts (the Plaid section is now titled "Linked banks (Plaid)") can't be import targets, since the next sync would update the same charges (409); their statements' look-alikes are checked instead (see Duplicates).
- **Flow:** `/import` (from **Import** in the Bank & card view, in Accounts & cards, or on an account, which passes `?account=`): choose the account (preselected: the one passed in, else the one imported into last; **Edit this account** and **Add an account or card** inline, the add form shown at once when there is none) → **Choose a file** (`<input type="file">`, `.csv,.ofx,.qfx`; works in the Android WebView) → the preview loads → **Import N transactions** → "Imported N transactions (M were already there)" with **See transactions**. The Transactions page shows the Receipts / Bank & card switch once any account exists, not only after linking; the Bank & card view has an **Import** chip, and **Sync** only when a bank is linked through Plaid.
- **Preview:** how the file was read in plain words ("Date: “Transaction Date” (month/day/year)", "Amount: “CAD$”, purchases are negative", or "Money out: “Column 3” · Money in: “Column 4”"), counts (new, already imported, to check, skipped) with the date range, the first 10 new rows as they will be saved (money out as an amount, money in as +amount), the possible duplicates with a checkbox each and the existing row they look like, and the skipped rows with line number and reason. When detection is unsure (`mapping_confident: false`) a notice asks to check, and the editor opens; **Change** opens it any time: date column and order (year-month-day, month/day/year, day/month/year), description column, and amounts in one column (with "purchases show as negative / positive") or two (money out, money in). Each option shows the column's first value as an example. The confirmed mapping is saved on the account (`import_format`, JSON, with the file's layout signature: its header names or its column count) and used first next time; a file with another layout is detected again.
- **Parsing** (backend, `app/imports/parser.py`; the file is never stored, not even in Drive):
  - The file is sent as base64 in JSON (like receipt photos), at most 2 MB and 5,000 rows (413 above that). Encoding UTF-8 (with or without BOM), else Windows-1252 (older French exports). Delimiter sniffed (comma, semicolon, tab).
  - Lines that aren't the table (BMO's "Following data is valid as of …", blank lines) are skipped: the table starts at the first row with the file's most common cell count. Its first row is a header when none of its cells is a date and fewer than two are amounts; headerless files (CIBC, TD) are mapped by content.
  - Roles by header name first (English and French: Transaction Date / Date / Date de l'opération, then Posting Date; Description / Description 1 + 2 / Name / Nom / Memo / Payee / Merchant / Libellé; Amount / Montant / Transaction Amount; Debit / Withdrawals / Retrait and Credit / Deposits / Dépôt; RBC's per-currency `CAD$` / `USD$` columns, the fuller one; Balance / Solde is ignored), then by content: the date column is the first where ≥ 90% of filled cells parse as dates (`YYYY-MM-DD`, `YYYYMMDD`, `M/D/YYYY`, `D/M/YYYY`, `Oct 5, 2026`, `05-Oct-2026`, French month names); day/month order comes from any value above 12, otherwise month first (North American banks) and unsure. Two neighbouring amount columns that are rarely both filled (≤ 5% of rows) and together cover ≥ 90% of rows are debit and credit (a balance column is always filled, so it never pairs); otherwise the first amount column is a signed amount (unsure when there are several). Description = the remaining text columns, joined, leaving out columns with one repeated value ("Visa") and card or account numbers (`4500********1234`).
  - Amounts are `Decimal`: `$`, `CAD`/`USD`, thousands separators, and spaces are dropped; `(12.34)`, a trailing `-`, and `CR` mean negative, `DR` positive; a decimal comma is accepted when the number ends in `,dd` (`1 234,56`, `1.234,56`). Stored with Plaid's sign: **positive = money spent, negative = money in**. Debit/credit → |debit| − |credit|; a signed column follows "purchases are negative/positive" (guessed: on a bank account money out is negative; on a credit card most rows are purchases, so the most common sign is theirs).
  - **OFX/QFX:** a small tolerant parser for `STMTTRN` blocks, SGML (OFX 1.x, leaf tags not closed) and XML (2.x): `DTUSER` (the purchase date) when present, else `DTPOSTED`; `TRNAMT` negated (OFX shows money out as negative); `NAME` + `MEMO`; `FITID`. `CURDEF` must equal the account's currency, otherwise the file is refused (422). No mapping is needed.
  - Rows with no readable date, no amount, amount 0, or no description are skipped and reported. A file without a date, amount, or description column is refused with a message that asks whether it is a bank statement (422).
- **Duplicates:** each row gets an `import_key`: `ofx:<FITID>` for OFX, otherwise `csv:` + SHA-256 (first 32 hex digits) of (account, date, amount, description lower-cased with spaces collapsed, n), where n counts identical rows earlier in the same file (two identical coffees on one day stay two rows, and re-importing an overlapping file finds both). A row whose key already exists among the active rows of the account, or of the same card's other accounts (a Plaid charge that replaced a statement row carries that row's key, phase 12), is **already imported** and never saved again (undone imports don't count). A new row with the same amount, a shared description word (`name_words`), and a date within 3 days of an existing row that no key matched (7 days when the existing row is a Plaid charge: Plaid has the purchase date, a CIBC file the posting date, up to 6 days later) is a **possible duplicate**, each existing row used once (the closest date first): shown unticked in the preview (collapsed when there are more than 5), saved only if the user ticks it (`include_lines`). Existing rows are the account's own (a bank that reformatted its export) and those of **the same card in other accounts**: a Plaid-linked or unlinked account, or one added twice by hand, with the same type and last 4 digits, or, when either has no last 4 digits, the same type and bank. So a card that Plaid already imported isn't imported again from its statement; the reason names the other account ("… in CIBC Aventura Visa •• 1234 (from Plaid)").
- **Saving:** the import parses the file again (the server never trusts a preview), then appends the new transactions in one `append_rows` (through `apply_bank_changes`) and the `imports` row in another; the account's `import_format` is rewritten only when it changed. New rows: a new UUID `transaction_id`, the account's currency, `pending` FALSE, `plaid_category` empty, `edited` FALSE, `import_id`, `import_key`. One import or bank sync at a time per user (`app/services/locks.py`).
- **Names:** `description` keeps the file's text; `merchant` is a name learned from receipts (`merchant_names`, by bank key), else the cleaned bank text (`clean_bank_text`), as for Plaid charges.
- **Kind and category**, first match wins:
  1. the remembered merchant category (`merchant_categories`, `find_rule`: by merchant key and bank key)
  2. the latest category of an earlier charge from the same merchant key or bank key that the user didn't choose (not `edited`), except spending in Other (a merchant the import couldn't tell is asked about again); so a merchant is categorized once, not on every import
  3. keyword rules (`app/imports/rules.py`), English and French: on a credit card, money in that says payment or transfer ("PAYMENT THANK YOU/PAIEMENT MERCI", "PAIEMENT REÇU", "AUTOPAY") → `transfer`; on a bank account, paying a card ("BILL PAY … CIBC VISA") → `transfer`, money in from pay, benefits, or interest ("PAY", "PAYROLL", "PAIE", "DIRECT DEPOSIT", "GST", "CCB", "INTEREST") → `income`, and transfers ("TRANSFER", "E-TRANSFER", "VIREMENT", "TFR") → `transfer`
  4. **Claude** (`ANTHROPIC_RECEIPT_MODEL`, effort `low`, structured output with a JSON schema, validated with Pydantic) for the remaining merchants, one entry per merchant key, at most 200 per request: the merchant name, the account type, and whether the money went out or in (**no amounts, dates, or account numbers**) → `{kind, category, subcategory}`, chosen from the user's categories and subcategories (descriptions included). Names are matched case-insensitively and saved with the sheet's spelling; an unknown category becomes Other and an unknown subcategory none.
  5. otherwise (no API key, or Claude failed): money out and card refunds → spending, Other; money in on a bank account → `income`. The import still succeeds.
  Changing a category afterwards works as for Plaid charges (Change category, remembered for the merchant).
- **After saving:** receipt matching runs (phase 10b rules), so imported charges that a receipt explains count once.
- **Undo:** each import in an account's history has **Undo**: the `imports` row and its transactions are soft-deleted (a deleted charge no longer counts or matches), and the file can be imported again.
- **Sample file:** **No statement handy? Try a sample file** on the Import page loads `frontend/public/sample-statement.csv` from the app itself: 40 made-up CIBC-style credit card rows (no header; date, description, debit, credit, masked card) from 2026-09-01 to 2026-10-20 (groceries, coffee, fuel, transit, subscriptions, a refund, two card payments), for testers without their own file. It imports like any file and can be undone.
- **Privacy:** the statement file is parsed on the backend and dropped; only parsed rows reach the user's Sheet. New merchant names (not amounts or dates) go to Anthropic to suggest categories; the Import page and the privacy policy say so.
- **Tests** (`tests/test_imports.py`): sample files shaped like CIBC (card and bank), TD, RBC, BMO, and a French Tangerine-style file in Windows-1252 with semicolons and decimal commas (made-up rows only, never real statements), OFX 1.x and 2.x, amounts in every supported style, dates, day/month ambiguity, saved and chosen mappings, keyword rules, re-importing an overlapping file, identical rows in one file, possible duplicates, Claude's answers checked against the user's categories (and failing), undo, receipt matching, and the API.

### Export (CSV)

`GET /api/export/csv` downloads the user's receipts for Excel, Numbers, or Google Sheets. Filename: `loonietoonie-<kind>-<today>.csv`.

- `kind=items` (default): one row per item: `date, store, item, category, subcategory, quantity, unit_price, amount (= quantity × unit_price), currency, receipt_total, source, receipt_id`. When a receipt's total differs from its items, a `(not itemized)` row keeps `amount` adding up to the receipt totals. With a `category` or `subcategory` filter, only matching items are included (and no `(not itemized)` rows).
- `kind=receipts`: one row per receipt: `date, store, total, currency, items (count), categories, photo (Drive link), source, receipt_id`. `source` is `receipt` or `bank` (a card/bank charge without a receipt, phase 10b). A `category` filter keeps receipts that have at least one item in it.
- Newest first; deleted rows are never exported. All currencies unless `currency` is given (each row says its currency; nothing is converted).
- UTF-8 **with a byte-order mark**, so Excel shows Japanese and French text correctly; `\r\n` line endings; ISO dates; amounts as plain decimals (`-1.00` for discounts).
- **CSV injection:** free-text cells (store, item, category) starting with `=`, `+`, `-`, `@`, tab, or CR get a leading `'`, so spreadsheet apps show them as text instead of running them as formulas. Number and date columns are never altered.

### Chat ("Ask", `/ask`)

Purpose: answer questions about actual spending ("What did I spend most on this month?", "今月の外食はいくら？") from the user's own receipts.

- **Model:** `ANTHROPIC_CHAT_MODEL` (default `claude-opus-5-5`, effort `medium`). `claude-haiku-5-5` is a much cheaper option. Refusal fallbacks are on: `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`) re-runs a safety-declined request on Anthropic's recommended model; the frontend drops any partial text on a `reset` event.
- **Numbers come from tools, not from the model's arithmetic.** Three read-only tools run pandas over the user's receipts (`app/services/spending_queries.py`), and their inputs are validated with Pydantic before running:
  - `spending_breakdown`: total and rows for a date range, grouped by category, subcategory (rows `Category › Subcategory`), store, month, item, or weekday; optional category, subcategory, store, and item filters
  - `find_purchases`: matching purchases, i.e. scanned receipts with their items and subcategories, and bank and card charges (newest first, up to 30) plus the count and total of all matches; same filters
  - `month_overview`: the dashboard numbers for one month (same code as `/api/summary`)
- **Prompt:** a stable system prompt (cached) plus per-user facts after the cache breakpoint: today, default currency, receipt counts per currency, first/last receipt date, and category names. Rules in the prompt:
  - one currency at a time
  - "so far" and same-days comparisons for the month in progress
  - answer in the user's language
  - short answers, using `- ` bullets and `**bold**` only (the frontend renders just these)
  - no unrequested saving advice
  - savings questions ("How could I save CA$3,000 in 6 months?"): turn the goal into an amount per month and suggest where it could come from in flexible categories (amounts compared with the usual month), leaving fixed costs such as rent alone; the app doesn't know income, so say it can't tell whether the goal fits overall. The Ask page offers "Where could I cut back to save $500 a month?" as an example question. This is a light test of interest in savings goals; a full goals feature (goal tracking, category limits) stays out of scope for now.
- **Loop:** streamed, at most 6 tool rounds per answer. Each tool round appends to the history unchanged (thinking blocks stay valid). The answer is checked for refusals and cut-off tool input before any tool runs, and unparseable tool JSON is retried at most twice.
- **Saved conversations, no server storage.** Completed answers are saved with their question in the user's spreadsheet (`chat_messages` tab, one row per message, soft-deleted). `POST /api/chat` takes an optional `conversation_id` and streams a `conversation` event first with the ID (a new UUID when none was sent); a failed or restarted answer isn't saved. Ask opens the latest conversation; **History** lists conversations (first question as the title, last message time) to reopen or delete, and **New chat** starts a fresh one. Only the open conversation is sent, as plain text, and only its latest 40 messages (starting with a question). Earlier answers are replayed without thinking blocks, which is allowed.
- **Privacy:** questions and the spending data the tools return are sent to Claude (Anthropic). The Ask page says so.
- **Tests:** a fake streaming client covers the loop; `RUN_LIVE_CLAUDE=1 pytest tests/live` asks the real model questions in English and Japanese about known receipts (a few cents per run).

### Dashboard ("Patterns", `/`)

Purpose: see actual spending patterns. Computed on the backend with pandas (`app/services/summary.py`) from all receipts plus card and bank charges that no receipt explains (phase 10b), served by `GET /api/summary`.

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
     - **drill-down:** a category whose items have subcategories opens (tap) into its subcategory bars for the month, with their own usual-month ticks, on the category's own scale; items without a subcategory show last as "No subcategory"
     - hover or keyboard focus shows this month, last month, and the usual month
  4. *Last 12 months*:
     - stacked columns of the top 5 categories (by 12-month spending), with smaller categories folded into a gray "Other categories" (`(other categories)` in the API, never equal to a real category name)
     - a legend, a tooltip listing every series, and a "Show table" view with all the numbers
  5. *Compared with similar households* (CAD only; `app/services/household.py`):
     - what the user spent per category since January 1 next to Statistics Canada's average for households like theirs over the same share of a year. The household type (living alone, couple, couple with children, single parent, couple living with others, other) and province come from **Your household** on the Account page. Until they're set, a banner at the top of Patterns (CAD) says what the comparison is and offers **Set up** (→ Account, `#household`) and **Not now** (remembered on that device in browser storage); after Not now, the card at this position invites the user instead
     - the averages are from the Survey of Household Spending (table 11-10-0222-01 by province, 11-10-0224-01 by household type, Canada only), built into `app/reference/household_spending.json` by `backend/scripts/build_household_spending.py`. A GitHub Actions workflow (`.github/workflows/household-spending.yml`) re-runs the script on the 1st of each month and opens an issue when Statistics Canada has published new data (a new survey or a new CPI year); updating the file stays a manual, reviewed change. A household type in a province is estimated as the Canadian average for the type times the province's ratio to Canada, and brought to the latest year's prices with the province's all-items CPI (table 18-10-0005-01). Territories use the Canada average
     - compared categories are those the app sees well, each mapped to survey lines: Housing (rented living quarters, or owned living quarters: mortgage payments, repairs, condo fees, property taxes, insurance; not utilities, second homes, or hotels), Groceries (food from stores), Dining Out (restaurants), Transportation (vehicle operation, leasing, local transit and taxis; not vehicle purchases), Utilities (water, fuel, electricity, communications), Household (furnishings, equipment, cleaning and paper supplies, computers, home entertainment equipment), Clothing, Entertainment & Hobbies together (recreation and reading, without package trips, recreational vehicle purchases, and electronics), Personal Care, Health (without insurance premiums), Pets, Education. Travel, Kids, Tax & Fees, and Other aren't compared
     - the period runs from January 1 (or the first CAD purchase this year, when records start later; the card then says "since <date>") to today. Similar households' amount is their yearly average × the share of the year covered (days ÷ 365 or 366); seasonal spending isn't modelled, and the card says so. Categories without spending in the period are left out (e.g. no pets)
     - each row: a bar for the user, a tick for similar households, and "N% more", "N% less", or "about the same" (within 5%). The card says the household, the province, the start date, the survey and price years, and that cash and unlinked cards make the user's amounts look lower; there is no overall "saved" figure
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
- Android app (Capacitor 8, `frontend/android/`, app ID `com.loonietoonie.app`): the same React code. Differences are kept small:
  - `src/platform.ts` (`isNativeApp`) chooses the bearer-token sign-in, ML Kit OCR, and CSV export through the share sheet (the WebView can't download with a token)
  - the service worker is registered only on the web, since the app ships its files in the APK
- PWA: manifest + service worker. Users can view past data offline (IndexedDB cache is optional). The service worker never serves `/api/*` from cache.
- In development, Vite proxies `/api/*` to the backend. In production, `VITE_API_BASE_URL` points to the backend.
- UI style: simple and friendly, with cute icons and a girl mascot character. The current icon is a placeholder.

## 8. Storage roadmap

| Stage | When | Storage | Notes |
| --- | --- | --- | --- |
| 1 | Now (phases 1–5) | Google Sheets only | Simple, free, user-owned data |
| 2 | Phase 6 (dashboard) or when speed matters | + on-device cache: Capacitor SQLite (`@capacitor-community/sqlite`) on Android, Dexie/IndexedDB on web PWA | Instant loads + offline. Sheets stays the source of truth; sync with `updated_at` / `deleted` (last-write-wins) |
| 3 | Bank/card linking (phase 10a, **in use**; from phase 12 only for users in `PLAID_ALLOWED_EMAILS` on the web app) | + small **encrypted server-side token store**: a SQLite file (`backend/data/tokens.sqlite3`, a Docker volume; git-ignored; back it up) | Holds only the user's Google ID (`sub`), Plaid item IDs, **AES-256-GCM-encrypted** access tokens (key `TOKEN_ENCRYPTION_KEY`, never in the database; user and item IDs are authenticated data), institution name, the sync cursor, and sync bookkeeping (`data_version`, when Plaid last read the bank, when a refresh was last requested; new columns are added on start). **No financial data**, no Google token, no spreadsheet ID: syncing only happens while the user's session is active. Bank accounts and transactions go to the user's Sheet (dedupe by `transaction_id`) |
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
| `backend/.env` (or repo-root `.env`; `backend/.env` wins) | `APP_ENV`, `FRONTEND_ORIGINS`, `FRONTEND_URL`, `ANTHROPIC_API_KEY`, `ANTHROPIC_RECEIPT_MODEL`, `ANTHROPIC_CHAT_MODEL`, `DAILY_SCAN_LIMIT` / `DAILY_ASK_LIMIT` / `DAILY_IMPORT_LIMIT` (Claude calls per user in any 24 hours; defaults 40 / 20 / 20, 0 = no limit), `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, `ALLOWED_EMAILS` (JSON list of Google emails that may sign in, invite-only launch; empty = anyone; `PLAID_ALLOWED_EMAILS` always included), `SESSION_SECRET` (32+ chars), `SESSION_MAX_AGE_DAYS`, `APP_REDIRECT_URI` (default `com.loonietoonie.app://auth/callback`), `APP_GOOGLE_REDIRECT_URI` (Google callback for Android app sign-ins when the phone reaches the backend at another address than the website; empty = `GOOGLE_REDIRECT_URI`), `DEFAULT_CURRENCY`, `PLAID_CLIENT_ID`, `PLAID_SECRET`, `PLAID_ENV` (`sandbox` / `production`), `PLAID_COUNTRY_CODES` (default `["CA","US"]`), `PLAID_ALLOWED_EMAILS` (JSON list of Google emails that may use Plaid on the web app, phase 12; empty = nobody), `TOKEN_ENCRYPTION_KEY` (32 random bytes, base64url), `TOKEN_STORE_PATH` (default `backend/data/tokens.sqlite3`; `/app/data/tokens.sqlite3` in Docker). Bank linking is off unless the Plaid keys and the encryption key are set, and from phase 12 only for allowlisted web users. |
| `frontend/.env` | `VITE_API_BASE_URL` (the Android app defaults to `http://localhost:8000` when empty), `VITE_DEV_BACKEND_URL` |
| `frontend/.env.android.local` | Android builds only (`vite build --mode android` in the `android:*` scripts): `VITE_API_BASE_URL` for the phone, e.g. the laptop's Tailscale HTTPS name |

`VITE_*` variables are visible in the browser bundle, so they never hold secrets.

External setup: an Anthropic API key, a Google Cloud project with the Sheets + Drive APIs enabled, a Google OAuth client of type **Web application** (with `GOOGLE_REDIRECT_URI` under "Authorized redirect URIs"), nothing for web OCR (Tesseract.js runs in the browser), and for bank linking a Plaid account (Sandbox keys for development; production needs Plaid's approval and is billed per linked item). The frontend does not need the Google client ID, because the backend runs the whole OAuth flow.

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
| 9 | Capacitor Android app (`com.loonietoonie.app`): ML Kit OCR behind `recognizeReceipt()`, system-browser sign-in with bearer tokens, CSV via the share sheet, mascot icon | Done |
| 10a | Bank and card linking: Plaid Link, encrypted token store (storage stage 3), import transactions into the Sheet, "Bank & card" view in Transactions, unlink | Done |
| 10b | Receipt matching (a receipt and its card charge count once; matched charges use the receipt's items), bank spending in Patterns, Ask, and CSV | Done |
| 11 | Statement import: file-import accounts, CSV and OFX/QFX parsing with column detection and a confirmed mapping, duplicate detection, categorization (remembered → keyword rules → Claude), import history with Undo, sample file (§7 Statement import) | Done |
| 12 | Plaid on the web only, for invited households: `PLAID_ALLOWED_EMAILS`, bank endpoints refused for the Android app and others, Plaid UI and `react-plaid-link` left out of the Android build (checked), Plaid-after-import duplicate merge (replaces the one-time cleanup); privacy policy, README, §8, §9, §12 (§7 Plaid on the web only) | Done |

## 11. Open questions

- **Play developer account type:** a personal account needs the 12-tester, 14-day closed test before production; an organization account (possible for a sole proprietor with a free D-U-N-S number, which can take a few weeks) does not. Check Google's current rules and decide before the closed test.

A savings step (budgets, saved amount) was once planned as phase 11 and dropped (2026-10-15); spreadsheets created earlier keep their unused `budgets` tab. Phase 11 is now statement import (decided 2026-10-22, so the app can be published on Google Play without bank linking). Phase 12 was first planned as removing Plaid; on 2026-10-23 it became keeping Plaid on the web app for invited households only.

## 12. Release checklist

Steps to take LoonieToonie from the local development setup (backend in Docker on the laptop, Android app over a USB `adb reverse` tunnel, Plaid Trial) to a release other people can use. Do them roughly in this order; each one is its own change.

### Backend hosting

Live since 2026-10-29 on **Fly.io, Toronto (`yyz`)**, invite-only: the API at https://api.loonietoonietracker.com (app `loonietoonie-tracker-api`, `backend/fly.toml`) and the web app at https://app.loonietoonietracker.com (app `loonietoonie-tracker-web`, `frontend/fly.toml`), both as CNAMEs at Porkbun with Fly certificates. Deploy from `backend/` or `frontend/` with `flyctl deploy --ha=false`. The backend image installs `backend/requirements.lock` (exact versions, so a rebuild can't pull in a new major release) and its entrypoint hands the root-owned volume to the unprivileged user. The laptop backend stays for development on Plaid Sandbox with its own `TOKEN_ENCRYPTION_KEY`; only production holds real bank tokens. Tailscale (`tailscale serve`) still reaches the laptop backend for testing backend changes on the phone.

- [x] Host the backend on **HTTPS** with a fixed domain (Fly.io Toronto, chosen for a Canadian region under PIPEDA / Law 25). Domain: **loonietoonietracker.com** (bought 2026-10-29 at Porkbun, DNS at Porkbun; `.ca` needs Canadian presence, which the developer doesn't meet).
- [x] Run **one instance** (`--ha=false`, `min_machines_running = 1`, never auto-stopped). The sync lock, `migrate_once`, the read cache, and the daily Claude limits live in process memory; several instances need those moved first.
- [ ] Give it a **persistent, backed-up volume** for `TOKEN_STORE_PATH` (the encrypted Plaid token store) and keep `TOKEN_ENCRYPTION_KEY` in the host's secret manager. Back up the volume and the key separately; losing the key means every user re-links their banks. Done so far: Fly volume `tokens` at `/app/data`, the key in Fly secrets (rotated 2026-10-29 by re-encrypting the stored tokens in place, no re-link). Still to do: confirm the volume's snapshot schedule and keep an offline copy of the key in the password manager.
- [x] Set production config: `APP_ENV=production` (secure cookies), `FRONTEND_URL`, `FRONTEND_ORIGINS` (web origin + `https://localhost` for the Android WebView), `GOOGLE_REDIRECT_URI`, `PLAID_ENV=production`, and real secrets from the secret manager (never baked into the image). The non-secret values are in `backend/fly.toml` `[env]`; the secrets are in Fly secrets.
- [x] Serve the **web app and the API on the same site** (e.g. `app.example.com` and `api.example.com`): the session cookie is `SameSite=Lax`, so a web app on a different site would not send it. (The Android app uses bearer tokens and is unaffected.)
- [ ] Logging without personal data (no receipt text, no transactions, no tokens), basic uptime monitoring, and spending limits on the Anthropic key. Done so far (2026-10-29): Anthropic workspace **LoonieToonie Tracker** with a US$100/month spend limit and email notifications; one key per environment (`loonietoonie-tracker-dev` in the laptop's `.env`, 90 days; `loonietoonie-tracker-prod` in Fly secrets, 1 year). Uptime monitoring since 2026-10-30: an external service checks `https://api.loonietoonietracker.com/api/health` and the web app every few minutes and emails on failure. Still to do: a log review for personal data.
- [x] Per-user daily limits on Claude calls, so closed-test users can't run up the Anthropic bill: each user may make `DAILY_SCAN_LIMIT` scan requests (default 40; an image retry counts too), `DAILY_ASK_LIMIT` Ask questions (20), and `DAILY_IMPORT_LIMIT` statement imports (20; counted only when Claude categorizes, previews are free) in any 24 hours; 0 turns a limit off. Over the limit, `POST /api/receipts/scan`, `POST /api/chat`, and `POST /api/imports` return **429** with `Retry-After` and a message the pages already show ("You've used all 40 receipt scans for today. You can try again in about 3 hours. You can still enter receipts by hand."). Counted after the request is validated, before Claude is called, in process memory over a rolling 24 hours (`app/services/usage.py`, like the sync lock: one instance; a restart forgets the counts). Also set a spending limit on the Anthropic key.
- [ ] Log Claude token usage per user and feature (from `response.usage`, no personal data) during the closed test, and compare Ask answers on Haiku 5.5 and on Sonnet 5.5 at low effort with Opus 5.5. Ask is the main per-user cost, so the cheapest model that answers well sets the Ask quotas and the subscription price. Stay with Claude (decided 2026-10-28; DeepSeek was considered and rejected for privacy).

### Web app

- [x] Deploy `frontend/` with `VITE_API_BASE_URL` pointing at the HTTPS backend: nginx on Fly.io (`frontend/Dockerfile`, `frontend/nginx.conf`; the build fails without the URL). `index.html`, the service worker, and the manifest are `no-cache`, so updates arrive on the next visit; hashed `/assets/` are cached for a year.
- [ ] `npm audit` and dependency updates before each release.

### Google sign-in

- [x] Add the production domain and `GOOGLE_REDIRECT_URI` to the OAuth client; add a home page, a privacy policy URL, and authorized domains to the consent screen. Done so far (2026-10-18): home page https://tomishing.github.io/Loonietoonie/ and privacy policy https://tomishing.github.io/Loonietoonie/privacy.html (GitHub Pages from `docs/`), authorized domain `tomishing.github.io`. Done 2026-10-29: redirect URI https://api.loonietoonietracker.com/api/auth/callback, JavaScript origin https://app.loonietoonietracker.com, authorized domain `loonietoonietracker.com`, app name LoonieToonie Tracker. Keep `docs/privacy.html` in step with the app's data flows.
- [x] **Publish** the OAuth app (Testing → In production; published 2026-10-18, no logo); in Testing mode refresh tokens expire after 7 days. The scopes (`openid`, `email`, `profile`, `drive.file`) should be non-sensitive; complete brand verification if a logo is shown.

### Plaid

- Plaid stays on the Trial (10 items) for the developer's household on the web app at launch (phase 12).
- [ ] Apply for Plaid **production access** in parallel (company profile, use case, security questionnaire; check whether a sole proprietor qualifies), and compare Pay-as-you-go and Growth per-account Transactions prices with the planned Plus price, and ask whether Transactions is billed per item (bank login) or per account so the Plus limit uses the same unit. On paid plans `/transactions/refresh` is billed per call, so limit the Sync button's refresh for paid users. Once approved, open web bank sync to Plus households (§7 Plaid on the web only). Plaid's Android SDK isn't needed: the Android app stays statement-only.
  Application in progress (2026-10-30): products **Transactions** and **Transactions Refresh** only (no Recurring Transactions, Balance, Auth, or Identity; recurring charges, if wanted, are computed from the Sheet for every source), industry "Budgeting and financial management tools". Security questionnaire answered honestly: consumer MFA before Plaid Link **No** (sign-in is Google OAuth, which the app can't verify for a second factor); MFA on critical systems **Yes, non-phishing-resistant** (Fly.io offers only authenticator-app codes).
- [ ] **Passkey step before Plaid Link** (web only), before bank sync opens to Plus households: the user registers a passkey (WebAuthn, platform authenticator) once, and Link, Reconnect, and linking another bank ask for it (a fresh assertion, e.g. within the last 10 minutes, checked on the backend before `POST /api/bank/link-token`). Passkey credentials (credential ID, public key, sign count) live in the server-side token store next to the Plaid items, never in the Sheet. Phishing-resistant, so the Plaid questionnaire's consumer-MFA answer can become "Yes, phishing-resistant" with a screenshot. Plan it as its own phase.
- [x] Set `PLAID_ALLOWED_EMAILS` on the production backend to the household's Google emails (Fly secrets, 2026-10-29).
- [ ] Set `ALLOWED_EMAILS` on the production backend to the household and the invited testers (invite-only launch; the gate is built, 2026-10-29; the household is on the list in Fly secrets, testers still to add). Open sign-in to everyone (empty list) only once the Anthropic spend limit, terms of service, and per-user usage logging are in place.
- [ ] Register an **HTTPS redirect URI** in the Plaid dashboard and pass it in `/link/token/create`, so banks that sign in through their own site (OAuth) work on the web app.
- [x] Plaid update mode for items that report `needs_relink`: **Reconnect** on the Account page (c0cfa5d).
- [ ] The privacy policy covers bank data from Plaid (web app; invited households at launch, premium households later).

### Android app

- [ ] Build with `VITE_API_BASE_URL` set to the HTTPS backend (the app otherwise talks to `http://localhost:8000`) and `NODE_ENV=production` for `cap sync` (turns off WebView debugging). Done so far (2026-10-29): the git-ignored `frontend/.env.android.local` points the debug build at https://api.loonietoonietracker.com. Still to do: `NODE_ENV=production` in the release build.
- [ ] Create an **upload keystore** outside the repository (`keytool`), keep it and its passwords in a password manager, and read them in Gradle from a git-ignored `keystore.properties` or environment variables. Use Play App Signing.
- [ ] Bump `versionCode` / `versionName` for every release; build an **Android App Bundle** (`./gradlew bundleRelease`). Play then ships per-ABI downloads, which removes most of the bundled ML Kit model size (the debug APK is ~49 MB).
- [ ] Google Play Console: a **personal** developer account requires a closed test with at least 12 testers opted in for 14 days in a row before production access; start it with the phase 11–12 build (or use an organization account, §11). Then: store listing (statement import, no bank linking), privacy policy, **Data safety** form for the Android app (financial info, photos, data flows to Google and Anthropic; no Plaid, which isn't in the Android app), content rating, current target API level, and a closed testing track before production.
- [ ] An in-app way to **delete server-side data** (Play requires one): Android users have none (no bank tokens; the session lives on the device), so the app explains that receipts and transactions live in the user's own Google Sheet and Drive and how to delete them. Allowlisted web users unlink banks to delete their tokens.

### Privacy and legal

- [ ] Privacy policy and terms (PIPEDA, Quebec Law 25): what is stored where (user's Sheet/Drive; encrypted bank tokens on the server; nothing else), what is sent to Anthropic (OCR text, the photo only on retry, chat questions and spending data) and to Plaid, no selling of data, no ads.
- [ ] Re-read the storage roadmap (§8): staying at stage 3 keeps the privacy promise; stage 4 still needs explicit approval and the privacy checklist.

### Before every release

- [ ] All checks from CLAUDE.md pass (backend, frontend, Android, Docker).
- [ ] Manual smoke test on a phone against the production backend: sign in, scan (single and multi-page), enter by hand, Patterns, Ask, CSV export, statement import (and Undo), receipt matching; on the web app with an allowlisted account: link/sync/unlink a bank.
