import uuid
from unittest.mock import AsyncMock, patch

import pytest

from .helpers import create_game_with_manual_unit

@pytest.mark.asyncio
async def test_log_unit_action_rush(client):
    """Test logging a rush action."""
    # Create game, join, and create a unit
    resp = await client.post(
        "/api/games",
        json={"name": "ActionTest", "player_name": "Host", "player_color": "#111111"},
    )
    code = resp.json()["code"]
    host_id = resp.json()["players"][0]["id"]
    
    join_resp = await client.post(
        f"/api/games/{code}/join",
        json={"player_name": "Guest", "player_color": "#222222"},
    )
    guest_id = join_resp.json()["players"][1]["id"]
    
    # Create a unit for host
    resp_unit = await client.post(
        f"/api/games/{code}/units/manual",
        json={
            "player_id": host_id,
            "name": "Test Unit",
            "quality": 4,
            "defense": 4,
            "size": 1,
            "tough": 1,
            "cost": 100,
        },
    )
    assert resp_unit.status_code == 201
    unit_id = resp_unit.json()["id"]
    
    # Create a unit for guest (required to start game)
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
    
    # Start the game
    await client.post(f"/api/games/{code}/start")
    
    # Log a rush action
    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        resp_action = await client.post(
            f"/api/games/{code}/units/{unit_id}/actions",
            json={"action": "rush"},
        )
        assert resp_action.status_code in (200, 201)
        data = resp_action.json()
        assert data["success"] is True
        assert "rushed" in data["message"].lower()
    
    # Check that event was created
    resp_events = await client.get(f"/api/games/{code}/events")
    assert resp_events.status_code == 200
    events = resp_events.json()
    rush_events = [e for e in events if e["event_type"] == "unit_rushed"]
    assert len(rush_events) == 1
    assert "rushed" in rush_events[0]["description"].lower()
    
    # Check that unit is activated
    resp_game = await client.get(f"/api/games/{code}")
    units = resp_game.json().get("units", [])
    unit = next((u for u in units if u["id"] == unit_id), None)
    assert unit is not None
    assert unit["state"]["activated_this_round"] is True


@pytest.mark.asyncio
async def test_log_unit_action_charge_with_targets(client):
    """Test logging a charge action with target units."""
    # Create game, join, and create units for both players
    resp = await client.post(
        "/api/games",
        json={"name": "ChargeTest", "player_name": "Host", "player_color": "#111111"},
    )
    code = resp.json()["code"]
    host_id = resp.json()["players"][0]["id"]
    
    join_resp = await client.post(
        f"/api/games/{code}/join",
        json={"player_name": "Guest", "player_color": "#222222"},
    )
    guest_id = join_resp.json()["players"][1]["id"]
    
    # Create unit for host
    resp_unit1 = await client.post(
        f"/api/games/{code}/units/manual",
        json={
            "player_id": host_id,
            "name": "Charging Unit",
            "quality": 4,
            "defense": 4,
            "size": 1,
            "tough": 1,
            "cost": 100,
        },
    )
    assert resp_unit1.status_code == 201
    unit1_id = resp_unit1.json()["id"]
    
    # Create unit for guest (target)
    resp_unit2 = await client.post(
        f"/api/games/{code}/units/manual",
        json={
            "player_id": guest_id,
            "name": "Target Unit",
            "quality": 4,
            "defense": 4,
            "size": 1,
            "tough": 1,
            "cost": 100,
        },
    )
    assert resp_unit2.status_code == 201
    unit2_id = resp_unit2.json()["id"]
    
    # Start the game
    await client.post(f"/api/games/{code}/start")
    
    # Log a charge action with target
    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        resp_action = await client.post(
            f"/api/games/{code}/units/{unit1_id}/actions",
            json={"action": "charge", "target_unit_ids": [unit2_id]},
        )
        assert resp_action.status_code in (200, 201)
        data = resp_action.json()
        assert data["success"] is True
        assert "charged" in data["message"].lower() or "charge" in data["message"].lower()
        assert "Target Unit" in data["message"]
    
    # Check that event was created with target info
    resp_events = await client.get(f"/api/games/{code}/events")
    assert resp_events.status_code == 200
    events = resp_events.json()
    charge_events = [e for e in events if e["event_type"] == "unit_charged"]
    assert len(charge_events) == 1
    assert "charged" in charge_events[0]["description"].lower()
    assert "Target Unit" in charge_events[0]["description"]
    assert charge_events[0]["details"] is not None
    assert "target_unit_ids" in charge_events[0]["details"]


@pytest.mark.asyncio
async def test_log_unit_action_invalid_action(client):
    """Test that invalid action types are rejected."""
    resp = await client.post(
        "/api/games",
        json={"name": "InvalidActionTest", "player_name": "Host", "player_color": "#111111"},
    )
    code = resp.json()["code"]
    host_id = resp.json()["players"][0]["id"]
    
    join_resp = await client.post(
        f"/api/games/{code}/join",
        json={"player_name": "Guest", "player_color": "#222222"},
    )
    guest_id = join_resp.json()["players"][1]["id"]
    
    # Create units for both players
    resp_unit = await client.post(
        f"/api/games/{code}/units/manual",
        json={
            "player_id": host_id,
            "name": "Test Unit",
            "quality": 4,
            "defense": 4,
            "size": 1,
            "tough": 1,
            "cost": 100,
        },
    )
    unit_id = resp_unit.json()["id"]
    
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
    
    # Start the game
    await client.post(f"/api/games/{code}/start")
    
    # Try invalid action
    resp_action = await client.post(
        f"/api/games/{code}/units/{unit_id}/actions",
        json={"action": "invalid_action"},
    )
    assert resp_action.status_code == 400


@pytest.mark.asyncio
async def test_log_unit_action_charge_requires_targets(client):
    """Test that charge/attack actions require targets."""
    resp = await client.post(
        "/api/games",
        json={"name": "ChargeTargetTest", "player_name": "Host", "player_color": "#111111"},
    )
    code = resp.json()["code"]
    host_id = resp.json()["players"][0]["id"]
    
    join_resp = await client.post(
        f"/api/games/{code}/join",
        json={"player_name": "Guest", "player_color": "#222222"},
    )
    guest_id = join_resp.json()["players"][1]["id"]
    
    # Create units for both players
    resp_unit = await client.post(
        f"/api/games/{code}/units/manual",
        json={
            "player_id": host_id,
            "name": "Test Unit",
            "quality": 4,
            "defense": 4,
            "size": 1,
            "tough": 1,
            "cost": 100,
        },
    )
    unit_id = resp_unit.json()["id"]
    
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
    
    # Start the game
    await client.post(f"/api/games/{code}/start")
    
    # Try charge without targets
    resp_action = await client.post(
        f"/api/games/{code}/units/{unit_id}/actions",
        json={"action": "charge"},
    )
    assert resp_action.status_code == 400


@pytest.mark.asyncio
async def test_cast_spell_success_deducts_tokens(client):
    """Casting a spell as success deducts tokens and logs the event."""
    code, host_id, unit_id = await create_game_with_manual_unit(
        client, is_caster=True, caster_level=2,
    )

    join_resp = await client.post(
        f"/api/games/{code}/join",
        json={"player_name": "Guest", "player_color": "#222222"},
    )
    assert join_resp.status_code == 201

    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        await client.post(f"/api/games/{code}/start")

    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        resp = await client.post(
            f"/api/games/{code}/units/{unit_id}/cast",
            json={"spell_value": 1, "spell_name": "Smite", "success": True},
        )
    assert resp.status_code == 201
    data = resp.json()
    assert data["success"] is True
    assert "succeeded" in data["message"]

    game = (await client.get(f"/api/games/{code}")).json()
    unit = next(u for u in game["units"] if u["id"] == unit_id)
    assert unit["state"]["spell_tokens"] == 1

    events = (await client.get(f"/api/games/{code}/events?limit=50")).json()
    cast_events = [e for e in events if e["event_type"] == "spell_cast"]
    assert len(cast_events) >= 1
    assert "Smite" in cast_events[0]["description"]


@pytest.mark.asyncio
async def test_cast_spell_failure_still_deducts_tokens(client):
    """A failed cast still deducts the token cost."""
    code, host_id, unit_id = await create_game_with_manual_unit(
        client, is_caster=True, caster_level=2,
    )

    join_resp = await client.post(
        f"/api/games/{code}/join",
        json={"player_name": "Guest", "player_color": "#222222"},
    )
    assert join_resp.status_code == 201

    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        await client.post(f"/api/games/{code}/start")

    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        resp = await client.post(
            f"/api/games/{code}/units/{unit_id}/cast",
            json={"spell_value": 1, "spell_name": "Smite", "success": False},
        )
    assert resp.status_code == 201
    data = resp.json()
    assert data["success"] is False
    assert "failed" in data["message"]

    game = (await client.get(f"/api/games/{code}")).json()
    unit = next(u for u in game["units"] if u["id"] == unit_id)
    assert unit["state"]["spell_tokens"] == 1


@pytest.mark.asyncio
async def test_cast_spell_insufficient_tokens(client):
    """Casting a spell with insufficient tokens is rejected."""
    code, host_id, unit_id = await create_game_with_manual_unit(
        client, is_caster=True, caster_level=1,
    )

    join_resp = await client.post(
        f"/api/games/{code}/join",
        json={"player_name": "Guest", "player_color": "#222222"},
    )
    assert join_resp.status_code == 201

    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        await client.post(f"/api/games/{code}/start")

    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        resp = await client.post(
            f"/api/games/{code}/units/{unit_id}/cast",
            json={"spell_value": 1, "spell_name": "Smite", "success": True},
        )
    assert resp.status_code == 201

    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        resp2 = await client.post(
            f"/api/games/{code}/units/{unit_id}/cast",
            json={"spell_value": 1, "spell_name": "Smite", "success": True},
        )
    assert resp2.status_code in (400, 422, 500)


@pytest.mark.asyncio
async def test_cast_spell_non_caster_rejected(client):
    """Non-caster units cannot cast spells."""
    code, host_id, unit_id = await create_game_with_manual_unit(
        client, is_caster=False, caster_level=0,
    )

    join_resp = await client.post(
        f"/api/games/{code}/join",
        json={"player_name": "Guest", "player_color": "#222222"},
    )
    assert join_resp.status_code == 201

    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        await client.post(f"/api/games/{code}/start")

    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        resp = await client.post(
            f"/api/games/{code}/units/{unit_id}/cast",
            json={"spell_value": 1, "spell_name": "Smite", "success": True},
        )
    assert resp.status_code in (400, 422, 500)
