# Testing

See **README.md** for full test coverage list and E2E notes. Summary:

## Backend (pytest)

```bash
uv run pytest tests/ -q
uv run pytest tests/api/games -v -k "manual_unit or rate_limit"   # filter tests
uv run pytest --cov=app --cov=tests --cov-report=term-missing
```

- **Location**: `tests/api/games/` — lifecycle, army import, unit state/combat/actions/spells, VP/round/events, solo/board. Shared helper: `tests/api/games/helpers.py`.
- **Config**: `tests/conftest.py` puts the project root on `sys.path`, uses SQLite and ASGITransport; no live DB or server required. `pyproject.toml` sets `[tool.pytest.ini_options] pythonpath = ["."]` for consistent imports.
- **CI**: `.github/workflows/deploy.yml` runs `scripts/build_gamestore.py --check` then pytest on `tests/api`, `tests/army_forge`, `tests/static`, and selected modules under `tests/`; deploy depends on the test job.
- **Coverage**: `pyproject.toml` sets `[tool.coverage.run] concurrency = ["greenlet", "thread"]` so line coverage includes async SQLAlchemy route handlers.

Covered areas include: game CRUD, join/start, manual units (including rules/loadout/upgrades), unit actions, event log export/clear, clear-events rate limiting (429 on 6th call per game per minute). Board layout and scroll behavior (e.g. panel scrolling) are not covered by API tests; verify manually or via E2E as needed.

## E2E (Playwright)

Requires app at `http://localhost:8000`. See README for `npm run test:e2e` and spec (`tests/e2e/join-import.spec.ts`).
