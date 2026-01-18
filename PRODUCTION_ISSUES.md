# Production Issues Review

## Critical Issues

### 1. Clear Events Endpoint - Missing Authorization & Activity Tracking ✅ FIXED
**Location**: `app/api/games.py:1678-1718`
**Status**: Activity tracking added
**Remaining**: 
- No authorization check - anyone with game code can clear events (low priority - game codes are effectively access tokens)
- No validation of game status (should prevent clearing in certain states?) - currently allows clearing at any time

### 2. Hardcoded Base Path
**Location**: `app/game/templates/game/board.html:1749-1753`
**Issue**: Hardcoded `/herald` path assumes specific deployment structure
**Risk**: Will break if deployed under different path or subdomain

**Current**:
```javascript
const getBasePath = () => {
    return (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1')
        ? ''
        : '/herald';
};
```

**Recommendation**: Use environment variable or detect from current URL path

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
**Location**: `app/api/games.py:1432-1434`
**Issue**: If `target_names` is empty (shouldn't happen due to validation, but defensive), `", ".join([])` returns empty string
**Current**: Works but could be clearer
**Status**: Actually safe - empty join returns empty string, which is fine

### 5. Alert() Usage for Errors
**Location**: Multiple places in `board.html`
**Issue**: Using `alert()` for error messages is not ideal UX
**Recommendation**: Consider using toast notifications or inline error messages

### 6. No Rate Limiting on Clear Events
**Location**: `app/api/games.py:1678`
**Issue**: Clear events endpoint could be spammed
**Recommendation**: Add rate limiting or require confirmation token

### 7. Missing Null Check in getAttachedHeroesForUnit
**Location**: `app/game/templates/game/board.html:1820-1823`
**Issue**: If `unitId` is null/undefined, filter will still run (returns empty array, which is safe)
**Status**: Actually safe - returns empty array if unitId is falsy

### 8. WebSocket Broadcast After Clear Events
**Location**: `app/api/games.py:1694-1700`
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
2. **Before Deploy**: Test base path detection in production environment  
3. **Post-Deploy**: Monitor for migration logs on startup (should see "Migrations completed successfully" or "All migrations are up to date")
4. **Future**: Replace alert() with better UX patterns
5. **Future**: Add rate limiting to destructive operations
6. **Future**: Consider adding authorization check to clear events endpoint (low priority)

## Test Coverage

All new features have test coverage:
- ✅ Unit action logging (rush, advance, hold, charge, attack)
- ✅ Target selection validation
- ✅ Event log export
- ✅ Clear events functionality

**Total tests**: 23 passing
