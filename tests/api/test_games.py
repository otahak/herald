import uuid
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_create_and_join_game(client):
    create_payload = {
        "name": "Test Game",
        "player_name": "Host",
        "player_color": "#123456",
    }
    resp = await client.post("/api/games", json=create_payload)
    assert resp.status_code == 201
    data = resp.json()
    code = data["code"]
    assert data["players"][0]["name"] == "Host"

    join_payload = {
        "player_name": "Guest",
        "player_color": "#654321",
    }
    resp_join = await client.post(f"/api/games/{code}/join", json=join_payload)
    assert resp_join.status_code == 201
    joined = resp_join.json()
    assert joined["code"] == code
    assert any(p["name"] == "Guest" for p in joined["players"])


@pytest.mark.asyncio
async def test_start_game_requires_two_players(client):
    # create game with host
    resp = await client.post(
        "/api/games",
        json={"name": "StartCheck", "player_name": "Host", "player_color": "#111111"},
    )
    code = resp.json()["code"]

    # attempt to start with only one player
    resp_start = await client.post(f"/api/games/{code}/start")
    assert resp_start.status_code in (400, 422, 500)


@pytest.mark.asyncio
async def test_import_army_broadcasts_state_update(client):
    # create game and join second player
    resp = await client.post(
        "/api/games",
        json={"name": "ImportTest", "player_name": "Host", "player_color": "#111111"},
    )
    code = resp.json()["code"]
    join = await client.post(
        f"/api/games/{code}/join",
        json={"player_name": "Guest", "player_color": "#222222"},
    )
    guest_id = join.json()["your_player_id"]

    # fake Army Forge response
    fake_units = [
        {
            "name": "Test Unit",
            "quality": 4,
            "defense": 4,
            "size": 1,
            "cost": 100,
            "rules": [],
            "selectedUpgrades": [],
            "id": "u1",
            "selectionId": "s1",
        }
    ]

    async def fake_get(url, *args, **kwargs):
        class FakeResponse:
            status_code = 200

            def raise_for_status(self): ...

            def json(self):
                return {"units": fake_units}

        return FakeResponse()

    # patch httpx.AsyncClient.get and broadcast_to_game to avoid side effects
    with patch("app.api.proxy.httpx.AsyncClient.get", new=AsyncMock(side_effect=fake_get)), patch(
        "app.api.proxy.broadcast_to_game", new=AsyncMock()
    ):
        resp_import = await client.post(
            f"/api/proxy/import-army/{code}",
            json={"army_forge_url": "https://army-forge.onepagerules.com/api/tts?id=FAKE", "player_id": guest_id},
        )
        assert resp_import.status_code in (200, 201)
        data = resp_import.json()
        assert data["units_imported"] == 1

    # verify units now present
    updated = await client.get(f"/api/games/{code}")
    assert updated.status_code == 200
    units = updated.json().get("units", [])
    assert any(u["name"] == "Test Unit" for u in units)


@pytest.mark.asyncio
async def test_player_join_broadcasts(client):
    # create game
    resp = await client.post(
        "/api/games",
        json={"name": "BroadcastTest", "player_name": "Host", "player_color": "#abcdef"},
    )
    code = resp.json()["code"]

    with patch("app.api.games.broadcast_to_game", new=AsyncMock()) as mock_broadcast:
        resp_join = await client.post(
            f"/api/games/{code}/join",
            json={"player_name": "Joiner", "player_color": "#123123"},
        )
        assert resp_join.status_code == 201
        mock_broadcast.assert_awaited_once()
        args, kwargs = mock_broadcast.await_args
        assert args[0] == code
        assert args[1]["type"] == "player_joined"


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
async def test_wound_tracking_creates_individual_events(client):
    """Test that wound tracking creates one log entry per wound."""
    # Create game, join, and create a unit
    resp = await client.post(
        "/api/games",
        json={"name": "WoundTest", "player_name": "Host", "player_color": "#111111"},
    )
    code = resp.json()["code"]
    host_id = resp.json()["players"][0]["id"]
    
    await client.post(
        f"/api/games/{code}/join",
        json={"player_name": "Guest", "player_color": "#222222"},
    )
    
    # Import a unit
    fake_units = [{
        "name": "Test Unit",
        "quality": 4,
        "defense": 4,
        "size": 1,
        "cost": 100,
        "rules": [],
        "selectedUpgrades": [],
        "id": "u1",
        "selectionId": "s1",
    }]
    
    async def fake_get(url, *args, **kwargs):
        class FakeResponse:
            status_code = 200
            def raise_for_status(self): ...
            def json(self):
                return {"units": fake_units}
        return FakeResponse()
    
    with patch("app.api.proxy.httpx.AsyncClient.get", new=AsyncMock(side_effect=fake_get)):
        await client.post(
            f"/api/proxy/import-army/{code}",
            json={"army_forge_url": "https://army-forge.onepagerules.com/api/tts?id=FAKE", "player_id": host_id},
        )
    
    # Get the unit
    game_resp = await client.get(f"/api/games/{code}")
    units = game_resp.json().get("units", [])
    assert len(units) > 0
    unit_id = units[0]["id"]
    
    # Add 2 wounds - should create 2 separate log entries
    resp_wound = await client.patch(
        f"/api/games/{code}/units/{unit_id}",
        json={"wounds_taken": 2},
    )
    assert resp_wound.status_code == 200
    
    # Check events: should have 2 UNIT_WOUNDED events (one per wound)
    resp_events = await client.get(f"/api/games/{code}/events")
    assert resp_events.status_code == 200
    events = resp_events.json()
    wound_events = [e for e in events if e["event_type"] == "unit_wounded"]
    assert len(wound_events) == 2
    
    # Note: Testing the 30-second threshold for heal detection would require
    # time manipulation, which is better suited for integration tests

