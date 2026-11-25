# PoolSmart — Project Report & Presentation Guide

This guide explains everything PoolSmart does, how it is architected, which algorithms and database tables power it, and how to demonstrate it live for a project report and slide presentation.

---

## 1. Project Overview & Problem Statement

### What Problem Does PoolSmart Solve?
University campuses located outside city centres (such as **SRM University-AP** in the **Guntur–Vijayawada–Amaravati** corridor) face daily commuting challenges:
- **High solo cab fares:** Students and staff travelling to the same transit hubs (railway stations, bus stands, airports, city centres) often book separate cabs.
- **Unverified / informal ride sharing:** Group chats lack route feasibility checks, detour guarantees, driver verification, and safety tracking.
- **Safety & time-window anxiety:** Riders catching trains/flights or travelling at night need strict arrival deadlines, verified co-riders for protected pools, and emergency alerting.

### What PoolSmart Does
**PoolSmart** is a full-stack web application and ride-pooling prototype that matches riders heading in compatible directions into shared cab trips **only when all constraints hold**:
1. **Verified Places & Service Area:** Validates pickup/drop-off points and enforces an 85 km service radius around campus.
2. **Constraint-Checked Route Pooling:** Respects vehicle seat capacity (max 4), pickup-before-drop order, maximum wait time, maximum detour ratio, arrival deadlines (`leave_by`), budget caps, and verified gender-pool preferences (`women_only`, `same_gender`).
3. **Optimal Matching Engine:** Uses **Exact Bitmask Dynamic Programming** (for clusters $\le 10$ requests) alongside **Greedy Insertion** (benchmarked in an Algorithm Lab) and **Hungarian Bipartite Assignment** to pair rider groups with approved, online drivers.
4. **End-to-End Trip Lifecycle:** Supports open departure discovery, 30-second driver offers, 4-digit OTP trip start verification, ordered stop completion, live trip sharing links, SOS alerts, UPI/Razorpay/Cash payments, PDF fare receipts, and an Admin Operations + Algorithm Lab dashboard.

---

## 2. Three Runtime Modes

| Mode | Command | Purpose | External Services |
|---|---|---|---|
| **Local Demo Mode** | `python app.py --demo` | Full end-to-end interactive simulator on `http://127.0.0.1:8000` using `.poolsmart-demo.db` and 13 fictional places | None (local `DemoMaps`, simulated weather, simulated payments/SOS/alerts) |
| **Read-Only Local Preview** | `python app.py --local-preview` | UI preview with illustrative SVG route map (`503` on interactive `/api/*` endpoints except `/api/healthz`) | None |
| **Live Production/Dev Mode** | `python app.py` | Full live operation with `.env` credentials | Google Maps (Places, Routes, Maps JS, Geocoding), SMTP, Open-Meteo, Web Push (VAPID), Razorpay/UPI, SOS Webhook/Telegram |

To reset and reseed the demo database cleanly (while port `8000` is stopped):
```powershell
python app.py --reset-demo
```

---

## 3. Technology Stack

- **Backend Framework:** Python 3.10+ (tested on 3.12), **FastAPI**, **Uvicorn** (single worker with `asyncio.Lock` around matching), **APScheduler** (`AsyncIOScheduler`).
- **Database & Migrations:** **SQLAlchemy 2.0**, **Alembic** (3 migrations: `0001_initial`, `0002_gender_verification`, `0003_one_payment_per_ride`), **SQLite** for local/demo (`poolsmart.db`, `.poolsmart-demo.db`) and **PostgreSQL** (`psycopg`) supported via `docker-compose.yml`.
- **Security & Validation:** **Pydantic v2** / `pydantic-settings`, **JWT (`HS256`)** via `python-jose`, **Fernet** symmetric encryption (`cryptography`) for trip start codes, `scrypt` password hashing, `hmac.compare_digest` for OTP & Razorpay webhooks, and **SlowAPI** rate limiting.
- **Utilities & External Integrations:** `httpx` (async HTTP), `reportlab` (PDF fare receipts), `qrcode[pil]` (UPI QR generation), `pywebpush` (VAPID Web Push), Google Maps APIs, Open-Meteo API.
- **Frontend:** Vanilla **HTML5 / CSS3 / ES6+ JavaScript** (`static/index.html`, `static/styles.css`, `static/app.js`), **PWA** support (`manifest.webmanifest`, `sw.js` service worker), and UI label translations in **English, Telugu (`తెలుగు`), and Hindi (`हिन्दी`)**.
- **Testing:** **Pytest** (`tests.py`) covering core algorithms, security validators, and full FastAPI `TestClient` end-to-end demo workflows.

---

## 4. System Architecture

```text
+-------------------------------------------------------------------+
|                   Browser / PWA (static/)                         |
|  Hero Map | 01 Account | 02 Rider | 03 Open Pools | 04 Driver     |
|           | 05 Admin Operations & Algorithm Lab                   |
+---------------------------------+---------------------------------+
                                  | HTTPS / REST JSON + WebSockets
                                  v
+-------------------------------------------------------------------+
|                   FastAPI Application (app/main.py)               |
|  - Auth & Profile (OTP / Demo Login / Admin Login, JWT Bearer)    |
|  - Ride Requests, Open Pools, Trip Tracking & Share Links         |
|  - Driver Onboarding, Online GPS/WS, Offers & Stop Completion     |
|  - Payments (UPI QR / Razorpay / Cash) & ReportLab PDF Receipts   |
|  - Safety (SOS, Night Emergency Contact Check, Route Deviation)   |
|  - Admin Operations & Algorithm Lab Metrics                       |
+---------------+---------------------------------+-----------------+
                |                                 |
                v                                 v
+-------------------------------+   +-------------------------------+
|  Matching & Algorithms Engine |   |  Maps & Simulator Layer       |
|  (app/matcher.py,             |   |  - GoogleMaps (app/maps.py)   |
|   app/algorithms.py)          |   |  - DemoMaps (app/demo.py)     |
|  - Clustering                 |   |  - TravelCache & MapsQuota    |
|  - Backtracking Route Search  |   +-------------------------------+
|  - Bitmask DP vs Greedy       |
|  - Hungarian Cab Assignment   |
+---------------+---------------+
                |
                v
+-------------------------------------------------------------------+
|        SQLAlchemy ORM & Alembic Migrations (app/models.py)        |
|        SQLite (.poolsmart-demo.db / poolsmart.db) or PostgreSQL   |
+-------------------------------------------------------------------+
```

---

## 5. Algorithms & Mathematical Logic (`app/algorithms.py` & `app/matcher.py`)

This section is ideal for the **Methodology / Algorithms** chapter of the report and presentation slides.

### Step 1: Request Clustering (`cluster_requests`)
Pending requests ready for dispatch are grouped by:
1. **Safety / Gender Key:** `("women", gender)`, `("gender", gender)`, or `("open", "")`.
2. **Time Band:** 15-minute buckets based on `scheduled_at` or `requested_at`.
3. **Direction Sector:** 45-degree bearing sectors measured from the campus coordinates to each drop-off location.
Clusters are chunked to **at most 10 requests** so exact optimization stays fast.

### Step 2: Feasibility & Constrained Stop Ordering (`best_route_backtracking`, `route_metrics`)
For any candidate group of $r$ riders, backtracking searches valid stop sequences with branch-and-bound pruning:
- **Worst-case complexity:** $O((2r)!)$ (with strict pruning when partial distance exceeds current best).
- **Precedence:** A rider's `pickup` must precede their `drop`.
- **Capacity:** Total onboard seats at every stop must satisfy $\sum \text{seats} \le 4$.
- **Wait Limit:** For every rider, $\text{pickup\_time} - \text{request\_time} \le \text{max\_wait\_minutes}$ (accounting for already-elapsed wait time and driver travel to pickup).
- **Detour Limit:** Ride duration must satisfy $\text{elapsed} \le \text{solo\_minutes} \times \text{max\_detour}$.
- **Arrival Deadline:** If `leave_by` is set, drop-off time must not exceed the deadline.

*(Additional reference routing algorithms in `app/algorithms.py`: **Dijkstra** in $O((V+E)\log V)$, **Floyd–Warshall** in $O(V^3)$ exposed to admins via `GET /api/graph`, and **Held–Karp DP** in $O(m^2 \cdot 2^m)$ for $m = 2r$ stops.)*

### Step 3: Exact Grouping vs. Greedy Baseline
Each cluster runs **both** grouping algorithms to record comparative metrics in the `matching_runs` table:
- **Exact Bitmask Set Partitioning (`optimal_grouping`):** Dynamic programming over bitmasks in $O(3^n)$ for $n \le 10$, partitioning riders into feasible cab groups with minimum total route distance.
- **Greedy Insertion (`greedy_grouping`):** $O(n^2)$ insertion attempts (excluding inner route checks), inserting each rider where added route distance is smallest; also serves as fallback if a cluster exceeds 10 requests.

### Step 4: Cab-to-Group Assignment (`hungarian` vs. `nearest_assignment`)
- Builds a cost matrix pairing each rider group with each available, approved, online driver (whose `location_at` is within the last 45 seconds and not busy on another trip/pending offer).
- Verifies vehicle `seat_capacity` and `driver_can_serve` (for `women_only` or `same_gender` pools, both riders and driver must have `gender_verified = True`).
- Runs **Hungarian Potentials Algorithm** in $O(k^3)$ to minimize total fleet travel meters, and logs the **Nearest Driver Greedy Baseline** ($O(g \cdot c)$) for comparison in the Admin Algorithm Lab.

### Step 5: Fare Calculation & Fair Cost Splitting (`fare_inr`, `split_fares`)
- **Base Formula:**
  $$\text{Fare} = (25.0 + 12.0 \times \text{distance\_km} + 1.0 \times \text{duration\_min}) \times \text{factor}$$
  where `factor = 1.2` between 9:00 PM and 6:00 AM IST (capped at `fare_surge_cap = 1.5`), otherwise `1.0`.
- **Proportional Split:** Shared trip fare is split across pooled riders proportionally to each rider's solo fare, and **capped so no rider ever pays more than their solo fare**. Any rider whose `budget_inr` is exceeded causes that assignment to be skipped.

---

## 6. Database Schema (16 Tables in `app/models.py`)

| Table | Purpose |
|---|---|
| `users` | Accounts (`rider`, `driver`, `admin`), gender & `gender_verified` flag, `women_only` / `same_gender` prefs, accessibility need, emergency contacts JSON, consent/verification timestamps |
| `otp_challenges` | Hashed 6-digit university email login codes, attempt counter (max 5), expiry & consumption timestamps |
| `admin_email_allowlist` | Invited non-`@srmap.edu.in` staff/alumni emails |
| `places` | Verified campus hubs and destinations (`place_id`, coordinates, `kind`) |
| `drivers` | Driver licence, vehicle number/model, seat capacity, insurance expiry, `approval_status`, `online` flag, latest GPS coordinates & timestamp |
| `ride_requests` | Rider pickup/drop, schedule or `leave_by`, seats, luggage, `max_wait_minutes`, `max_detour`, `budget_inr`, gender flags, `status`, `trip_id`, `fare_inr` |
| `trips` | Assigned trip state (`offered`, `accepted`, `started`, `completed`, `expired`, `declined`, `cancelled`), encrypted & hashed 4-digit start OTP, encoded polyline, distance, duration, fare total, deviation/stop timestamps |
| `trip_stops` | Ordered pickup/drop stops (`sequence`, `ride_id`, `place_id`, `action`, `eta_at`, `completed_at`) |
| `driver_offers` | Time-limited (30s) trip offers sent to drivers (`state`: `pending`, `accepted`, `declined`, `expired`, `cancelled`) |
| `travel_cache` | 15-minute time-banded route matrix cache (`duration_s`, `distance_m`, `estimated`) |
| `maps_quota` | Daily Google Maps element & request counter to enforce `MAPS_DAILY_ELEMENT_LIMIT` |
| `matching_runs` | Benchmark log per matching batch (`greedy_cost_m`, `exact_cost_m`, `solo_cost_m`, `nearest_cost_m`, `hungarian_cost_m`, `greedy_ms`, `exact_ms`, `requests_count`) |
| `push_subscriptions` | Validated browser Web Push endpoints (`fcm`, `apple`, `mozilla`, `windows`) and VAPID keys |
| `safety_incidents` | SOS incident logs with coordinates, details, and delivery timestamp |
| `payments` | One payment record per ride (`uq_payments_ride_id`), method (`upi`, `razorpay`, `cash`), state (`pending`, `due`, `awaiting_confirmation`, `paid`, `failed`) |
| `audit_logs` | Administrative and safety audit trail (`gender_verification`, `driver_review`, `driver_suspended`, `upi_payment_confirmed`, `route_deviation_alert`, `long_stop_alert`, `account_deleted`) |

---

## 7. Security, Privacy & Safety Features

- **Campus-Scoped Authentication:** Live OTP login restricts sign-ups to `@srmap.edu.in` or admin-allowlisted addresses; sessions use short-lived HS256 JWTs stored in `sessionStorage` per browser tab.
- **Protected Pools Without Document Storage:** `women_only` and `same_gender` pools require `gender_verified = True` (set by an administrator after institutional verification—no ID documents are uploaded or stored in the app). Changing gender resets `gender_verified` to `False`.
- **Night Safety Rule:** Rides scheduled or requested between **9:00 PM and 5:00 AM IST** require an emergency contact email on file; in live mode, accepting a night trip emails an expiring `/share/{token}` tracking link to emergency contacts.
- **Active Trip Monitoring & SOS:** WebSocket driver location updates (`/ws/driver`) check for >300m route deviation sustained for 60s and >5-minute unscheduled stops; riders can copy an expiring share link or trigger SOS (`/api/rides/{id}/sos`).
- **Privacy Controls:** Users can export all their data as JSON (`/api/me/export`) or anonymize/delete their account (`DELETE /api/me`). Location sharing stops when the driver goes offline or the trip ends.
- **HTTP Hardening:** Strict Content-Security-Policy, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`, and endpoint rate limits via `slowapi`.

---

## 8. Live Demo Walkthrough Script (For Your Presentation!)

Start the server with:
```powershell
python app.py --demo
```
Open **`http://127.0.0.1:8000`**. Because sessions are stored in `sessionStorage` (tab-isolated), you can either switch accounts in one tab or open **two side-by-side browser tabs** (Rider in Tab 1, Driver in Tab 2) to show real-time updates!

### Demo Accounts (Password for all: `poolsmart-demo`)
- `rider.one@poolsmart.demo` — **Asha Demo** (Female rider, pending gender verification)
- `rider.two@poolsmart.demo` — **Dev Demo** (Male rider, gender verified)
- `driver@poolsmart.demo` — **Sam Driver** (Approved driver, initially offline)
- `driver.pending@poolsmart.demo` — **Pat Applicant** (Pending driver application)
- `driver.new@poolsmart.demo` — **Lee New Driver** (Has not applied yet)
- `admin@poolsmart.demo` — **PoolSmart Admin** (Administrator)

### Step-by-Step Live Demo Flow (5–7 Minutes)
1. **Landing Page & Bilingual Support:** Show the hero map, simulated network status, and switch language (`EN` / `తెలుగు` / `हिन्दी`).
2. **Account & Profile (`01 / YOUR ACCOUNT`):** Sign in as `rider.one@poolsmart.demo` (`Asha Demo`). Show profile fields, emergency contacts, `EXPORT MY ACCOUNT DATA`, and the gender-verification notice.
3. **Request First Ride (`02 / RIDER`):** Click Pickup -> select **Demo Hostel**; click Drop-off -> select **Demo Mall**; click **FIND A CAB**. The ride appears as `PENDING` (no driver is online yet).
4. **Open Pools & Joining (`03 / DEPARTURES`):** Sign out and sign in as `rider.two@poolsmart.demo` (`Dev Demo`). Under **Open Pools**, point out the forming departure to **Demo Mall (`1 rider · 3 seats shown free`)**. Click **SET DESTINATION**, choose **Demo Main Gate** as Pickup, and submit **FIND A CAB**.
5. **Driver Goes Online & Pool Matching (`04 / DRIVER`):** Sign out and sign in as `driver@poolsmart.demo` (`Sam Driver`). Click **GO ONLINE**. Within seconds, the matching engine pools both riders into one trip and displays a single offer (`2 riders`, `pickup: Demo Hostel / pickup: Demo Main Gate / drop: Demo Mall / drop: Demo Mall`). Click **ACCEPT**.
6. **Start OTP, Live Trip & Stop Completion:**
   - Sign in as `rider.one@poolsmart.demo` (or view in Tab 1) to see the assigned driver, vehicle number, ordered stop ETAs, **4-digit START CODE**, **SHARE LIVE TRIP**, **SOS**, and **CALL DRIVER**.
   - As `driver@poolsmart.demo`, enter the 4-digit start code -> **START TRIP**, then click **COMPLETE STOP** for all 4 stops in sequence.
7. **Payments, Cash Collection & PDF Receipt:**
   - As `rider.one@poolsmart.demo`, click **PAY / RECEIPT** on the completed ride, select **Cash**, pay (`DUE`), then as `driver@poolsmart.demo` click **MARK CASH COLLECTED** (`PAID`). Download the generated **PDF Receipt**.
   - Optionally pay Rider B's ride via **UPI**, then sign in as `admin@poolsmart.demo` to confirm the UPI payment.
8. **Admin Operations & Algorithm Lab (`05 / OPERATIONS`):** As `admin@poolsmart.demo`, show live metrics (`COMPLETED KM`, `RIDERS IN COMPLETED POOLS %`), approve a pending driver or verify Asha's gender, and show the **Algorithm Lab** table comparing `GREEDY · M` vs `EXACT · M`, `DP GAP %`, and execution time in milliseconds.

---

## 9. Suggested Presentation Slide Deck (10–12 Slides)

1. **Title Slide:** PoolSmart — Constraint-Aware Campus Ride Pooling for SRM University-AP.
2. **Problem & Motivation:** Cost of solo trips, safety gaps in informal ride-sharing, strict campus transit deadlines.
3. **System Objectives:** Cost reduction via pooling, safety-first verification, deterministic feasibility guarantees, and live/demo parity.
4. **System Architecture:** Frontend PWA + FastAPI + Matching Engine + Google Maps / DemoMaps + SQLAlchemy/Alembic.
5. **User Roles & Workflows:** Rider, Open Pools, Driver, and Admin Operations.
6. **Matching Pipeline & Algorithms:** Clustering -> Backtracking ($O((2r)!)$) -> Bitmask DP ($O(3^n)$) vs Greedy ($O(n^2)$) -> Hungarian ($O(k^3)$).
7. **Fair Fare Splitting & Constraints:** Night surge cap, proportional solo-capped fare splitting, detour/wait/arrival-deadline enforcement.
8. **Database Design:** Overview of the 16 normalized SQLAlchemy tables.
9. **Safety, Security & Privacy:** `@srmap.edu.in` OTP, admin gender verification, 4-digit start OTP, route-deviation & long-stop alerts, SOS, data export/deletion.
10. **Live Demo / Screenshots:** Show Rider -> Open Pools -> Driver Offer -> Trip Execution -> Payment & PDF Receipt -> Admin Algorithm Lab.
11. **Testing & Evaluation:** 16 automated `pytest` unit + end-to-end tests (`tests.py`) and Algorithm Lab DP-gap benchmarking.
12. **Current Limitations & Future Scope:** Single-worker process lock (needs distributed lock for multi-worker scaling), friend-group invites, recurring schedules, automated UPI bank reconciliation, rider ratings, and production load testing.

---

## 10. Verification & Key Files Reference

Run the automated test suite (`tests.py`):
```powershell
python -m pytest tests.py -v
```
All **16 test cases** cover: Dijkstra reachability, backtracking pickup-before-drop precedence, onboard seat capacity, exact bitmask DP vs. solo cost, Hungarian assignment, detour & live-leg limits, departure deadlines, elapsed wait times, gender-verified pool checks, push subscription URL validation, failed payment retry idempotency, and full FastAPI end-to-end demo workflows.

### Key Files
- [app.py](file:///c:/Users/Arjun/Documents/PoolSmart/app.py) / [run.py](file:///c:/Users/Arjun/Documents/PoolSmart/run.py) — Service launcher (`--demo`, `--reset-demo`, `--local-preview`)
- [app/main.py](file:///c:/Users/Arjun/Documents/PoolSmart/app/main.py) — FastAPI routes, WebSockets, lifespan scheduler, and middleware
- [app/algorithms.py](file:///c:/Users/Arjun/Documents/PoolSmart/app/algorithms.py) — Graph, backtracking, Held–Karp, greedy/bitmask grouping, and Hungarian assignment
- [app/matcher.py](file:///c:/Users/Arjun/Documents/PoolSmart/app/matcher.py) — Batch matching pipeline, clustering, gender checks, and fare splitting
- [app/models.py](file:///c:/Users/Arjun/Documents/PoolSmart/app/models.py) & [app/schemas.py](file:///c:/Users/Arjun/Documents/PoolSmart/app/schemas.py) — 16 SQLAlchemy tables and Pydantic schemas
- [app/demo.py](file:///c:/Users/Arjun/Documents/PoolSmart/app/demo.py) — Demo seed data and `DemoMaps` simulator
- [app/maps.py](file:///c:/Users/Arjun/Documents/PoolSmart/app/maps.py) — Google Places/Routes/Geocoding integration, caching, and quota fallback
- [static/index.html](file:///c:/Users/Arjun/Documents/PoolSmart/static/index.html), [static/styles.css](file:///c:/Users/Arjun/Documents/PoolSmart/static/styles.css), [static/app.js](file:///c:/Users/Arjun/Documents/PoolSmart/static/app.js) — Single-page PWA frontend
- [tests.py](file:///c:/Users/Arjun/Documents/PoolSmart/tests.py) — Unit and end-to-end test suite
