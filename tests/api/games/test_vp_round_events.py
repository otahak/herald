import uuid
from unittest.mock import AsyncMock, patch

import pytest

@pytest.mark.asyncio
async def test_victory_points_tracking(client):
    """Test VP tracking with log consolidation."""
    # Create game and join second player
    resp = await client.post(
        "/api/games",
        json={"name": "VPTest", "player_name": "Host", "player_color": "#111111"},
    )
    code = resp.json()["code"]
    host_id = resp.json()["players"][0]["id"]
    
    join = await client.post(
        f"/api/games/{code}/join",
        json={"player_name": "Guest", "player_color": "#222222"},
    )
    
    # Add 2 VP
    resp_vp = await client.patch(
        f"/api/games/{code}/players/{host_id}/victory-points",
        json={"delta": 2},
    )
    assert resp_vp.status_code == 200
    assert resp_vp.json()["victory_points"] == 2
    
    # Check events: should have 2 VP_CHANGED events
    resp_events = await client.get(f"/api/games/{code}/events")
    assert resp_events.status_code == 200
    events = resp_events.json()
    vp_events = [e for e in events if e["event_type"] == "vp_changed"]
    assert len(vp_events) == 2
    
    # Remove 1 VP - should delete one event
    resp_vp2 = await client.patch(
        f"/api/games/{code}/players/{host_id}/victory-points",
        json={"delta": -1},
    )
    assert resp_vp2.status_code == 200
    assert resp_vp2.json()["victory_points"] == 1
    
    # Check events: should have 1 VP_CHANGED event remaining
    resp_events2 = await client.get(f"/api/games/{code}/events")
    assert resp_events2.status_code == 200
    events2 = resp_events2.json()
    vp_events2 = [e for e in events2 if e["event_type"] == "vp_changed"]
    assert len(vp_events2) == 1


@pytest.mark.asyncio
async def test_round_tracking(client):
    """Test round tracking with +/- interface."""
    # Create game, join, and start
    resp = await client.post(
        "/api/games",
        json={"name": "RoundTest", "player_name": "Host", "player_color": "#111111"},
    )
    code = resp.json()["code"]
    
    await client.post(
        f"/api/games/{code}/join",
        json={"player_name": "Guest", "player_color": "#222222"},
    )
    
    # Start game (sets round to 1)
    await client.post(f"/api/games/{code}/start")
    
    # Increment round
    resp_round = await client.patch(
        f"/api/games/{code}/round",
        json={"delta": 1},
    )
    assert resp_round.status_code == 200
    assert resp_round.json()["current_round"] == 2
    
    # Decrement round (should not go below 1)
    resp_round2 = await client.patch(
        f"/api/games/{code}/round",
        json={"delta": -1},
    )
    assert resp_round2.status_code == 200
    assert resp_round2.json()["current_round"] == 1
    
    # Try to go below 1
    resp_round3 = await client.patch(
        f"/api/games/{code}/round",
        json={"delta": -1},
    )
    assert resp_round3.status_code == 200
    assert resp_round3.json()["current_round"] == 1  # Should stay at 1


@pytest.mark.asyncio
async def test_export_events(client):
    """Test exporting events as markdown."""
    resp = await client.post(
        "/api/games",
        json={"name": "ExportTest", "player_name": "Host", "player_color": "#111111"},
    )
    code = resp.json()["code"]
    host_id = resp.json()["players"][0]["id"]
    
    join_resp = await client.post(
        f"/api/games/{code}/join",
        json={"player_name": "Guest", "player_color": "#222222"},
    )
    guest_id = join_resp.json()["players"][1]["id"]
    
    await client.post(
        f"/api/games/{code}/units/manual",
        json={
            "player_id": host_id,
            "name": "Host Unit",
            "quality": 4,
            "defense": 4,
            "size": 1,
            "tough": 1,
            "cost": 100,
        },
    )
    await client.post(
        f"/api/games/{code}/units/manual",
        json={
            "player_id": guest_id,
            "name": "Guest Unit",
            "quality": 4,
            "defense": 4,
            "size": 1,
            "tough": 1,
            "cost": 100,
        },
    )
    
    # Create some events by starting the game
    await client.post(f"/api/games/{code}/start")
    
    # Export events
    resp_export = await client.get(f"/api/games/{code}/events/export")
    assert resp_export.status_code == 200
    assert "text/markdown" in resp_export.headers.get("content-type", "")
    assert f"game-{code}-events.md" in resp_export.headers["content-disposition"]
    
    content = resp_export.text
    assert "Game Log:" in content
    assert code in content
    assert "Events" in content


@pytest.mark.asyncio
async def test_clear_events(client):
    """Test clearing all events."""
    resp = await client.post(
        "/api/games",
        json={"name": "ClearEventsTest", "player_name": "Host", "player_color": "#111111"},
    )
    code = resp.json()["code"]
    host_id = resp.json()["players"][0]["id"]
    
    join_resp = await client.post(
        f"/api/games/{code}/join",
        json={"player_name": "Guest", "player_color": "#222222"},
    )
    guest_id = join_resp.json()["players"][1]["id"]
    
    # Create units for both players
    await client.post(
        f"/api/games/{code}/units/manual",
        json={
            "player_id": host_id,
            "name": "Host Unit",
            "quality": 4,
            "defense": 4,
            "size": 1,
            "tough": 1,
            "cost": 100,
        },
    )
    await client.post(
        f"/api/games/{code}/units/manual",
        json={
            "player_id": guest_id,
            "name": "Guest Unit",
            "quality": 4,
            "defense": 4,
            "size": 1,
            "tough": 1,
            "cost": 100,
        },
    )
    
    # Create some events by starting the game
    await client.post(f"/api/games/{code}/start")
    
    # Verify events exist
    resp_events = await client.get(f"/api/games/{code}/events")
    assert resp_events.status_code == 200
    events_before = resp_events.json()
    assert len(events_before) > 0
    
    # Clear events
    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        resp_clear = await client.delete(f"/api/games/{code}/events")
        assert resp_clear.status_code == 200
        data = resp_clear.json()
        assert data["success"] is True
        assert data["deleted_count"] > 0
    
    # Verify events are gone
    resp_events_after = await client.get(f"/api/games/{code}/events")
    assert resp_events_after.status_code == 200
    events_after = resp_events_after.json()
    assert len(events_after) == 0


@pytest.mark.asyncio
async def test_clear_events_rate_limited(client):
    """Clear events is rate-limited (5 per minute per game); 6th call returns 429."""
    resp = await client.post(
        "/api/games",
        json={"name": "RateLimitTest", "player_name": "Host", "player_color": "#111111"},
    )
    code = resp.json()["code"]
    host_id = resp.json()["players"][0]["id"]
    join_resp = await client.post(
        f"/api/games/{code}/join",
        json={"player_name": "Guest", "player_color": "#222222"},
    )
    guest_id = join_resp.json()["players"][1]["id"]
    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        for _ in range(2):
            await client.post(
                f"/api/games/{code}/units/manual",
                json={"player_id": host_id, "name": "H", "quality": 4, "defense": 4, "size": 1, "tough": 1, "cost": 0},
            )
            await client.post(
                f"/api/games/{code}/units/manual",
                json={"player_id": guest_id, "name": "G", "quality": 4, "defense": 4, "size": 1, "tough": 1, "cost": 0},
            )
        await client.post(f"/api/games/{code}/start")
        for _ in range(5):
            r = await client.delete(f"/api/games/{code}/events")
            assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        r6 = await client.delete(f"/api/games/{code}/events")
    assert r6.status_code == 429
    assert "detail" in r6.json() or "Too many" in r6.text
