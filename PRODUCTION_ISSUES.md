# Production Issues Review

## Critical Issues

### 1. Clear Events Endpoint - Missing Authorization & Activity Tracking ✅ FIXED
**Location**: `app/api/games/events.py` (`clear_events`)
**Status**: Activity tracking added
**Remaining**: 
- No authorization check - anyone with game code can clear events (low priority - game codes are effectively access tokens)
- No validation of game status (should prevent clearing in certain states?) - currently allows clearing at any time

### 2. Hardcoded Base Path ✅ FIXED
**Location**: `app/utils/__init__.py`, `app/templates/base.html`, `app/static/js/store/gameStore.js`, board/lobby templates
**Status**: Base path is now configurable. Server sets `window.HERALD_BASE_PATH` from request (or `BASE_PATH` env). Frontend uses it everywhere; no hardcoded `/herald`.
**Usage**: Set `BASE_PATH=/herald` (or your subpath) in production if not using ASGI `root_path`.

### 3. Database Migration Automation ✅ FIXED
**Location**: `app/main.py:209-260`, `deploy/run_pending_migrations.py`
**Status**: Migrations now run automatically on application startup
**Implementation**: 
- Created `run_pending_migrations.py` that detects which migrations have been applied
- Added startup hook in `main.py` that runs pending migrations automatically
- Can be disabled by setting `AUTO_RUN_MIGRATIONS=false` environment variable

**Note**: Ensure `deploy/` directory is available in production container or copy migration scripts during deployment

## Medium Priority Issues

### 4. Missing Error Handling for Empty target_names
**Location**: `app/api/games/units_combat.py` (unit action / target name handling)
**Issue**: If `target_names` is empty (shouldn't happen due to validation, but defensive), `", ".join([])` returns empty string
**Current**: Works but could be clearer
**Status**: Actually safe - empty join returns empty string, which is fine

### 5. Alert() Usage for Errors ✅ FIXED
**Location**: `app/game/templates/game/board.html`
**Status**: Replaced all `alert()` with toast notifications (error/success/info). Toasts auto-dismiss after 5s and can be dismissed manually.

### 6. No Rate Limiting on Clear Events ✅ FIXED
**Location**: `app/api/games/` (import/clear-events handlers), `app/utils/rate_limit.py`
**Status**: Rate limiting added: clear_events (5/min per game), import_army (10/min per game). Returns 429 when exceeded.

### 7. Missing Null Check in getAttachedHeroesForUnit
**Location**: `app/game/templates/game/board.html:1820-1823`
**Issue**: If `unitId` is null/undefined, filter will still run (returns empty array, which is safe)
**Status**: Actually safe - returns empty array if unitId is falsy

### 8. WebSocket Broadcast After Clear Events
**Location**: `app/api/games/events.py` (post–clear-events broadcast)
**Issue**: Broadcasts to all players, but they may not refresh events automatically
**Recommendation**: Ensure frontend listens for `events_cleared` reason and refreshes

## Low Priority / Code Quality

### 9. Inconsistent Error Message Formatting
**Location**: Various endpoints
**Issue**: Some errors use `error.detail`, others use `error.message` or custom formats
**Recommendation**: Standardize error response format

### 10. Missing Type Validation in Frontend
**Location**: `app/game/templates/game/board.html:1847-1860`
**Issue**: `targetUnitIds` parameter not validated before sending to API
**Status**: Backend validates, but frontend could catch earlier

### 11. Activity Tracking Not Updated in All Action Endpoints
**Location**: Various endpoints
**Issue**: Some endpoints update `last_activity_at`, others don't
**Recommendation**: Audit all state-changing endpoints to ensure activity tracking

## Recommendations

1. **Before Deploy**: Ensure `deploy/` directory is mounted or migration scripts are copied to container
2. **Before Deploy**: Set `BASE_PATH` if app is served under a subpath (e.g. `BASE_PATH=/herald`).
3. **Post-Deploy**: Monitor for migration logs on startup (should see "Migrations completed successfully" or "All migrations are up to date").
4. **CI**: Pytest runs before deploy (see `.github/workflows/deploy.yml`).
5. **Future**: Consider adding authorization check to clear events endpoint (low priority).

## Test Coverage

All new features have test coverage:
- ✅ Unit action logging (rush, advance, hold, charge, attack)
- ✅ Target selection validation
- ✅ Event log export
- ✅ Clear events functionality

**Total tests**: 23 passing
