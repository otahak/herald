# Testing

See **README.md** for full test coverage list and E2E notes. Summary:

## Backend (pytest)

```bash
uv run pytest tests/api
uv run pytest tests/api -v -k "manual_unit or rate_limit"   # filter tests
uv run pytest --cov=app --cov=tests/api --cov-report=term-missing
```

- **Location**: `tests/api/test_games.py` (games API and WebSocket-related flows).
- **Config**: `tests/conftest.py` uses SQLite and ASGITransport; no live DB or server required.
- **CI**: `.github/workflows/deploy.yml` runs `uv run pytest tests/api` before deploy; deploy job depends on test job.

Covered areas include: game CRUD, join/start, manual units (including rules/loadout/upgrades), unit actions, event log export/clear, clear-events rate limiting (429 on 6th call per game per minute). Board layout and scroll behavior (e.g. panel scrolling) are not covered by API tests; verify manually or via E2E as needed.

## E2E (Playwright)

Requires app at `http://localhost:8000`. See README for `npm run test:e2e` and spec (`tests/e2e/join-import.spec.ts`).
