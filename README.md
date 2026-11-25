# PoolSmart

PoolSmart is a live ride-pooling prototype for SRM University-AP and the Guntur–Vijayawada–Amaravati region. Riders choose real places, set waiting and detour limits, and receive a cab offer only when an approved, online driver can serve the route. Drivers share GPS while online and during an active trip. This repository is a software project, not an already-operated transport service.

## What it does

- University-email OTP sign-in, administrator accounts, account export and deletion.
- Google Places validation and Google Routes traffic-aware road times; service-area checks use verified campus coordinates.
- Constrained pickup/drop ordering, capacity checks, wait and detour checks, greedy grouping, exact bitmask grouping for up to 10 requests, and cab assignment.
- Open departure listings, scheduled departure or arrival deadline, estimated fares, trip offers, ordered stop completion, live map tracking, and time-limited trip links.
- Driver application review, consent-based GPS sharing while online, SOS delivery to a configured HTTPS response channel, emergency contact sharing for late-night rides, web-push notifications, and route/long-stop alerts.
- UPI QR and Razorpay test-mode orders, signed Razorpay webhook verification, cash collection confirmation, INR fare statements, and administrator UPI confirmation.
- Campus weather advice from Open-Meteo, a responsive installable web shell, and English/Hindi/Telugu translation for the main navigation and section headings.
- An administrator view of driver applications, gender verification requests, payment confirmations, matching runs, and service counts.

Live mode uses configured Google Maps and mail/payment/push services. It does not fabricate riders, vehicles, map coordinates, or route results. If a Google Routes quota limit is reached, coordinate-based distance/time estimates are explicitly labeled as estimates.

## Local demo mode (no external credentials)

Run the end-to-end local simulator without creating a `.env` file:

```powershell
python app.py --demo
```

Open `http://127.0.0.1:8000`. The page displays **LOCAL DEMO — simulated data** while the simulator is active. The fictional place network, routes, travel times, weather, driver location, offers, and payment/SOS responses are labelled as simulated. Demo mode does not contact Google, email, push, payment, or emergency-response providers. Demo records and authentication tokens use a separate `.poolsmart-demo.db`; live records remain in the configured live database.

Choose a role from the demo account selector and use the local-only password `poolsmart-demo`:

| Role | Account |
|---|---|
| Rider A | `rider.one@poolsmart.demo` |
| Rider B | `rider.two@poolsmart.demo` |
| Approved driver (offline) | `driver@poolsmart.demo` |
| Driver pending review | `driver.pending@poolsmart.demo` |
| New driver application | `driver.new@poolsmart.demo` |
| Administrator | `admin@poolsmart.demo` |

These are public demonstration credentials for this local simulator only. Never reuse them for a live deployment. Reset only the demo database and reseed the sample accounts with:

```powershell
python app.py --reset-demo
```

Stop the demo server before resetting. The reset command refuses to remove the demo database while port 8000 is occupied. `python app.py --demo` always binds to `127.0.0.1`; live development also defaults to localhost. A deployment must explicitly configure its host and secure infrastructure rather than relying on these local defaults.

The local simulator exercises the shared account, ride, driver, matching, trip, fare, and operations workflows, but its results are not real dispatch, routing, weather, GPS, payments, notifications, or emergency response. Live mode remains a separate credentialed setup and never falls back to demo data.

## Requirements before starting

Use Python 3.10 or later. A first live start requires valid credentials; `python app.py` exits with actionable setup instructions if they are missing. To configure:

1. Copy `.env.example` to `.env`.
2. In Google Cloud, enable Places API (New), Routes API, Geocoding API, and Maps JavaScript API. Use a server key restricted to the server and required APIs, and a separate browser key restricted by HTTPS referrer and Maps JavaScript API. Configure a Map ID. Review Google Maps storage and billing terms before deployment.
3. Configure an SMTP account with STARTTLS for university-email OTP and trip-link messages.
4. Configure an HTTPS SOS webhook that is staffed and tested. PoolSmart records the incident and reports delivery failure, but it does not replace local emergency services.
5. Configure HTTPS `PUBLIC_BASE_URL`, exact `CORS_ORIGINS`, a random `JWT_SECRET` of at least 32 characters, and VAPID keys for push notifications.
6. Configure either a UPI payee ID/name or Razorpay. Razorpay test mode is on by default and rejects a live key until test mode is disabled deliberately. Add a Razorpay webhook secret to verify payment events. UPI QR payments remain pending until an administrator checks the bank statement and confirms them.
7. For production, set `DATABASE_URL` to PostgreSQL and provide `POSTGRES_PASSWORD` to Docker Compose. SQLite is the local default, not a recommended concurrent production database.

Keep `.env` and all credentials out of version control. The map API key is necessarily visible to browsers; protect it with referrer/API restrictions and quotas. Do not start a public service until driver checks, emergency escalation, privacy retention, insurance, local transport law, payment settlement, and support operations have been reviewed by the institution.

For a no-credential visual check, run `python app.py --local-preview`. This read-only mode binds to `127.0.0.1:8000`, uses an explicitly illustrative route diagram, and disables booking, accounts, maps, payments and driver tools. It is not a ride service.

## Run

From the `PoolSmart` directory:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
# Fill in the required settings in .env before continuing.
python app.py
```

`python app.py` runs migrations and starts the live FastAPI service at `http://127.0.0.1:8000` by default. Live startup fails closed when credentials are missing. For an explicitly configured deployment, terminate TLS at a trusted reverse proxy, set `HOST` deliberately, and set `PUBLIC_BASE_URL`, `CORS_ORIGINS`, and `FORWARDED_ALLOW_IPS` to the actual deployment values.

To run the read-only local preview instead, omit credential setup and use:

```powershell
python app.py --local-preview
```

The launcher intentionally runs one Uvicorn worker. Matching coordination currently uses a process-local lock; do not increase the worker count or run multiple app instances until that lock is replaced with shared coordination.

Create the first administrator interactively:

```powershell
python manage.py create-admin --email admin@example.edu --name "Service administrator"
```

Administrator passwords are entered interactively and are never placed on the command line. To allow staff/alumni email addresses, use `python manage.py allow-email --email person@example.edu --role staff`.

## Tests

```powershell
python -m pytest -q
# Or: python tests.py
```

The focused tests cover Dijkstra, precedence, cab capacity, exact-versus-greedy grouping, Hungarian assignment, detour and deadline feasibility, protected-pool verification, and unreachable nodes. They use synthetic fixtures isolated in `tests.py`; no fixture data is loaded into the service.

## Algorithm notes

Algorithm logic is written in Python in `app/algorithms.py`; Python/SQLAlchemy/Google SDKs provide infrastructure and route data, not shortcuts for these methods.

| Technique | Implementation | Complexity |
|---|---|---|
| Graph / greedy | Dijkstra with a min-heap | `O((V + E) log V)` |
| Graph / dynamic programming | Floyd–Warshall all-pairs alternative | `O(V^3)` |
| Backtracking | Pickup/drop order search with precedence, capacity, wait, detour and current-best pruning | `O((2r)!)` worst case |
| Dynamic programming | Held–Karp comparison for `m = 2r` stops | `O(m^2 * 2^m)` |
| Greedy | Least-increase insertion of compatible requests | `O(n^2)` insertion attempts, excluding route search |
| Dynamic programming | Bitmask set partitioning for at most 10 requests | `O(3^n)` partition states |
| Assignment | Hand-coded Hungarian potentials method | `O(k^3)` |
| Greedy baseline | Nearest available driver assignment | `O(g*c)` for `g` groups and `c` cabs |

Real rides are routed through Google's road graph. Dijkstra and Floyd–Warshall are retained as algorithm demonstrations and are not used to invent a road network for live dispatch. Exact grouping is bounded because the dial-a-ride problem is NP-hard. The service may leave a valid request pending if no live driver can satisfy its constraints.

## API overview

Authenticated endpoints use a bearer token. Coordinates, place IDs, schedules, and payment methods are validated server-side.

| Method and path | Purpose |
|---|---|
| `GET /api/map/config`, `GET /api/places/hubs` | Browser map configuration and verified campus hubs |
| `GET /api/places/suggest?q=...`, `POST /api/places/resolve` | Google Places search and server-side place verification |
| `POST /api/auth/request-otp`, `POST /api/auth/verify-otp`, `POST /api/auth/admin-login` | Rider and administrator sign-in |
| `GET/PATCH/DELETE /api/me`, `GET /api/me/export` | Profile, data export and account deletion |
| `POST /api/rides`, `GET /api/rides`, `DELETE /api/rides/{id}` | Request, view and cancel a ride |
| `GET /api/pools/open`, `POST /api/pools/{id}/join` | See and request to join a forming departure; a match is not guaranteed |
| `/api/driver/*`, `/api/drivers/*`, `/ws/driver` | Driver application, availability, offers, stop completion and GPS |
| `/api/trips/*`, `/ws/trip/{id}`, `/share/{token}` | Rider trip state, live location and expiring share links |
| `/api/rides/{id}/payment`, `/api/rides/{id}/receipt` | Payment initiation and PDF fare statement |
| `/api/rides/{id}/sos`, `/api/push/subscribe` | Trip safety alert and browser notifications |
| `/api/admin/*` | Restricted operations and review endpoints |

## Evaluation plan

1. Run the tests and verify feasibility before comparing costs.
2. Use real, authorized Google Routes data and compare greedy grouping with bitmask DP on the same small requests.
3. Measure the effect of wait/detour limits and assignment choices; include unmatched requests rather than hiding them.
4. Track pending requests, provider quota, estimate labels, dispatch latency, pool rate, delivery failures, and completed trips.
5. Load-test with synthetic data in a separate test environment before any live campus trial. Do not use fabricated results in the operations dashboard.

## Current limitations

- This is a prototype foundation. No live credentials, campus approval, verified driver roster, security assessment, incident staffing, or payment account are included in this repository.
- Gender-matched requests are blocked until an administrator verifies the self-declared profile against the institution's approved process. The application does not collect identity documents.
- The open-departures board groups by destination area, but final matching can still reject a proposed join based on timing, seats, route feasibility, gender preference, or driver availability.
- Friend-group invitations, recurring ride schedules, automated UPI reconciliation, fare disputes/refunds, ratings, and production load testing are not implemented.
- Hindi and Telugu currently cover primary navigation and section headings; detailed forms and safety/payment messages remain in English.
- Browser UI uses Google Maps Places widgets and therefore requires a valid browser key, HTTPS in production, and supported Google Maps APIs.
- Live matching is serialized within one application process; multi-worker or multi-instance deployment needs a distributed lock before it is safe.
