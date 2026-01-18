# Herald

Multiplayer-synced digital scoreboard for One Page Rules (Grimdark Future / Firefight). Browser-based SPA served by a Litestar backend with WebSockets for real-time sync, Vue 3 + Tailwind/DaisyUI frontend, and PostgreSQL (tests auto-use SQLite).

## Features
- Create/join games via code; player identity persistence with selection modal
- Real-time updates over WebSockets (player join, state updates)
- Unit tracking: wounds/models, activation, morale threshold; transports; limited weapons
- **Victory Points**: Manual +/- interface with log consolidation (removals delete corresponding "add" entries)
- **Round Tracker**: Manual +/- interface for round tracking
- **Time-based Wound Tracking**: Wounds removed within 30 seconds delete the log entry (quick corrections); wounds removed after 30 seconds log as heals
- **Attached Units**: Heroes attached to parent units are visually grouped and controlled together; single activate button for combined units
- **Unit Detachment**: Manual detachment of heroes from parent units; automatic detachment when parent is destroyed
- **Shaken/Unshaken Logging**: All shaken state changes are logged with proper event tracking
- **Unit Action Logging**: Log unit actions (Rush, Advance, Hold, Charge, Attack) with target selection for Charge/Attack actions
- Action log: Automatic logging of all game state changes with human-readable descriptions
- **Event Log Export**: Export game event log as markdown file
- **Clear Event Log**: Clear all events from a game (with confirmation)
- **Army Forge Import**: Import units from Army Forge share links (accumulates with existing units)
- **Manual Unit Entry**: Add units one at a time via form modal
- **Clear All Units**: Explicit button with confirmation to clear all units (only in lobby)
- **Solo Play Mode**: Play games solo with an "Opponent" player; game state persistence with save/load
- **Game Expiration**: Multiplayer games expire after 1 hour of inactivity; solo games expire after 30 days
- **Automated Migrations**: Database migrations run automatically on application startup
- Responsive/mobile-friendly UI

## Architecture
- Backend: Python 3.12+, Litestar, SQLAlchemy (advanced-alchemy), Postgres (asyncpg). Entry `app/main.py`; routes in `app/routes.py`.
- Frontend: Vue 3 (CDN), TailwindCSS + DaisyUI, Jinja templates. Screens in `app/game/templates/game/board.html` and `lobby.html`.
- Realtime: WebSockets via `app/api/websocket.py` (`state`, `state_update`, `player_joined`, `player_left`).
- State store: `app/static/js/store/gameStore.js` (fetches, identity persistence, WS handling).
- Data models: `app/models/*` (Game, Player, Unit, UnitState, GameEvent).

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
- **Automated Migrations**: Migrations run automatically on application startup (can be disabled with `AUTO_RUN_MIGRATIONS=false`)

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

Test coverage includes:
- Unit action logging (rush, advance, hold, charge, attack)
- Target selection validation for charge/attack actions
- Event log export functionality
- Clear events functionality

### End-to-End (Playwright)
Requires server at `http://localhost:8000`.
```bash
npm run test:e2e        # headless
npm run test:e2e:headed # headed/debug
```
Spec `tests/e2e/join-import.spec.ts`: host creates, guest joins via code, modal dismissal, army import sync (host sees units). Skips on CI by default.

## Key Endpoints
- `POST /api/games` – create game (game_system optional, defaults to GFF; `is_solo` optional for solo play mode)
- `POST /api/games/{code}/join` – join game
- `POST /api/games/{code}/start` – start game
- `POST /api/proxy/import-army/{code}` – import Army Forge list (`army_forge_url`, `player_id`) - **adds units** (does not clear existing)
- `POST /api/games/{code}/units/manual` – create unit manually (`CreateUnitRequest`)
- `POST /api/games/{code}/units/{unit_id}/actions` – log unit action (`action`: rush/advance/hold/charge/attack, `target_unit_ids` optional for charge/attack)
- `DELETE /api/games/{code}/players/{player_id}/units` – clear all units for a player (lobby only)
- `DELETE /api/games/{code}/events` – clear all events for a game
- `GET /api/games/{code}` – fetch game state (players, units, events)
- `GET /api/games/{code}/events` – fetch game event log
- `GET /api/games/{code}/events/export` – export game events as markdown file
- `PATCH /api/games/{code}/players/{player_id}/victory-points` – update VP (`delta: int`)
- `PATCH /api/games/{code}/round` – update round (`delta: int`)
- `PATCH /api/games/{code}/units/{unit_id}` – update unit state (wounds, activation, etc.)
- `PATCH /api/games/{code}/units/{unit_id}/detach` – manually detach a hero from its parent unit
- `POST /api/games/{code}/save` – save game state (solo mode)
- `GET /api/games/{code}/saves` – list saved game states
- `POST /api/games/{code}/load` – load a saved game state
- `GET /ws/game/{code}` – WebSocket (messages: `state`, `state_update`, `player_joined`, `player_left`, `error`)

## Frontend State / Identity
- `gameStore.js` persists identity in localStorage; honors `?playerId=` in board URL.
- Board modal rules: empty slot → name entry; disconnected player → selection; both active → blocked.

## Army Management
- **Army Forge Import**: Imports units from Army Forge share links. Units are **added** to existing units (does not clear existing units). Multiple imports accumulate.
- **Manual Unit Entry**: Add units one at a time via the "Add Unit Manually" button in the lobby. Units accumulate with imported units.
- **Clear All Units**: Use the "Clear All Units" button (only visible when player has units) to remove all units. Requires confirmation modal. Only available in lobby status. Resets player stats (unit count, points, army name).

## Unit Actions
- **Action Logging**: When activating a unit, select an action (Rush, Advance, Hold, Charge, or Attack)
- **Target Selection**: For Charge and Attack actions, select one or more opposing units as targets
- **Action Events**: All actions are logged in the event log with descriptions like "Unit charged Target Unit" or "Unit advanced"
- **Attached Units**: When a unit with attached heroes performs an action, the heroes are automatically activated but don't get separate action logs

## Event Log Management
- **Event Log**: All game state changes are automatically logged with human-readable descriptions
- **Export Log**: Export the entire event log as a markdown file for record-keeping
- **Clear Log**: Clear all events from a game (requires confirmation). Useful for resetting the log mid-game.

## Solo Play Mode
- **Solo Games**: Create games with `is_solo: true` to play against yourself
- **Opponent Player**: Solo games automatically create an "Opponent" player that you can control
- **Save/Load**: Save game states at any point and load them later to resume play
- **No WebSockets**: Solo games don't use WebSockets for real-time sync (uses polling instead)
- **Game Persistence**: Solo games persist for 30 days of inactivity before expiring

## Game Expiration
- **Multiplayer Games**: Expire after 1 hour of no connected users or activity
- **Solo Games**: Expire after 30 days of no activity
- **Expired Games**: When accessed, all buttons are disabled and a modal indicates expiration
- **Log Retention**: Event logs remain available for 24 hours after expiration

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
