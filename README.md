# Herald

Multiplayer-synced digital scoreboard for One Page Rules (Grimdark Future / Firefight). Browser-based SPA served by a Litestar backend with WebSockets for real-time sync, Vue 3 + Tailwind/DaisyUI frontend, and PostgreSQL (tests auto-use SQLite).

## Features
- Create/join games via code; player identity persistence with selection modal
- Real-time updates over WebSockets (player join, state updates)
- Unit tracking: wounds/models, activation, morale threshold; transports; limited weapons
- Objectives: neutral/seized/contested; round/turn tracking; action log
- Army Forge import (share link) per player with log entry
- Responsive/mobile-friendly UI

## Architecture
- Backend: Python 3.12+, Litestar, SQLAlchemy (advanced-alchemy), Postgres (asyncpg). Entry `app/main.py`; routes in `app/routes.py`.
- Frontend: Vue 3 (CDN), TailwindCSS + DaisyUI, Jinja templates. Screens in `app/game/templates/game/board.html` and `lobby.html`.
- Realtime: WebSockets via `app/api/websocket.py` (`state`, `state_update`, `player_joined`, `player_left`).
- State store: `app/static/js/store/gameStore.js` (fetches, identity persistence, WS handling).
- Data models: `app/models/*` (Game, Player, Unit, UnitState, Objective, GameEvent).

## Project Layout
- `app/main.py` – Litestar app wiring (DB plugin, templates, routes)
- `app/api/` – REST + WebSocket handlers (`games.py`, `proxy.py`, `websocket.py`)
- `app/game/templates/game/` – UI templates (`board.html`, `lobby.html`)
- `app/static/js/store/` – frontend reactive store + WS client
- `tests/api/` – backend tests
- `tests/e2e/` – Playwright E2E (requires running server)
- `Justfile` – developer commands

## Prerequisites
- Python 3.12+
- Node 18+ (for Playwright E2E)
- Postgres for normal runs; SQLite auto-used for tests
- `uv` recommended for Python env/deps

## Setup
```bash
uv sync                       # Python deps (dev included)
npm install                   # Playwright test deps
npx playwright install        # one-time browser install for E2E
```

## Running the App (dev)
```bash
# default DATABASE_URL in app/main.py: postgresql+asyncpg://postgres:postgres@db:5432/herald
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```
Or via Docker Compose:
```bash
docker compose up --build
```

## Production Deployment

See `deploy/README.md` for full deployment guide to DigitalOcean or similar VPS.

Quick summary:
- Systemd service (`deploy/herald.service`) runs the app
- Nginx reverse proxy (`deploy/nginx.conf`) handles HTTP/WebSocket
- Automated deploy script (`deploy/deploy.sh`) sets up everything
- Database initialization script (`deploy/init_db.py`) creates schema

For a 2GB/1vCPU droplet, the service runs 2 uvicorn workers and binds to localhost (nginx handles external traffic).

## Just Commands
- `just`                 – list recipes
- `just init`            – build Docker image
- `just start`           – docker compose up -d
- `just stop`            – docker compose down
- `just restart`         – rebuild + restart stack
- `just logs`            – tail logs for `web` (override with `service=...`)
- `just sh`              – shell into web container
- `just psql`            – psql into db
- `just test`            – backend pytest + Playwright E2E  
  - `just test py_args="-k join"` (pytest flags)  
  - `just test e2e_args="--headed"` (Playwright flags)  
  - `E2E=0 just test` (skip E2E)

## Tests
### Backend (pytest)
```bash
uv run pytest tests/api
uv run pytest --cov=app --cov=tests/api --cov-report=term-missing
```
Notes: SQLite via `DATABASE_URL` override in `tests/conftest.py`; ASGITransport runs app in-process; lifecycle via `asgi-lifespan`.

### End-to-End (Playwright)
Requires server at `http://localhost:8000`.
```bash
npm run test:e2e        # headless
npm run test:e2e:headed # headed/debug
```
Spec `tests/e2e/join-import.spec.ts`: host creates, guest joins via code, modal dismissal, army import sync (host sees units). Skips on CI by default.

## Key Endpoints
- `POST /api/games` – create game
- `POST /api/games/{code}/join` – join game
- `POST /api/games/{code}/start` – start game
- `POST /api/proxy/import-army/{code}` – import Army Forge list (`army_forge_url`, `player_id`)
- `GET /api/games/{code}` – fetch game state (players, units, objectives)
- `GET /ws/game/{code}` – WebSocket (messages: `state`, `state_update`, `player_joined`, `player_left`, `error`)

## Frontend State / Identity
- `gameStore.js` persists identity in localStorage; honors `?playerId=` in board URL.
- Board modal rules: empty slot → name entry; disconnected player → selection; both active → blocked.

## Troubleshooting
- WebSockets: if stale, hard-refresh; check console for “WebSocket connected.”
- Army import: verify Army Forge link; backend logs show httpx status; backend tests stub the call.
- DB: ensure `DATABASE_URL` is reachable (tests override to SQLite).

## Deployment Sizing (rough guide)
- ~100 concurrent users: 2 vCPU / 4 GB RAM, 20–40 GB SSD. Examples: DigitalOcean Basic 2vCPU/4GB, AWS t4g.small or t3.small. Single-node app+DB is fine.
- ~1000 concurrent users: 4 vCPU / 8 GB RAM, 40–80 GB SSD. Examples: DigitalOcean 4vCPU/8GB, AWS t4g.medium or m6g.medium (ARM), t3.medium (x86). Consider managed Postgres or separate DB, and add a load balancer if running multiple app nodes; ensure WebSocket stickiness or shared room registry.
- WebSockets are async-friendly; prioritize lean payloads and 1 worker per vCPU. Bandwidth/fan-out matter more than CPU for typical usage. Managed Postgres or a separate DB host helps avoid contention at higher loads.

## To-Do / Next Steps
- Testing: increase backend coverage (edge cases, error paths), run Playwright E2E regularly, and add CI.
- Stability: monitor WebSocket reliability and DB performance; add load tests for WebSocket fan-out and import flows.
- Performance: consider Redis/shared room registry and load balancer for higher concurrency; evaluate managed Postgres as load grows.
- UX/Polish: broaden playtests, refine mobile UX, and add clearer status/connection indicators.
