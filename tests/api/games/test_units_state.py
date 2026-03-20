import uuid
from unittest.mock import AsyncMock, patch

import pytest

from .helpers import create_game_with_manual_unit

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
    
    with patch("app.army_forge.import_service.httpx.AsyncClient.get", new=AsyncMock(side_effect=fake_get)):
        await client.post(
            f"/api/proxy/import-army/{code}",
            json={"army_forge_url": "https://army-forge.onepagerules.com/share?id=FAKE12345", "player_id": host_id},
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


@pytest.mark.asyncio
async def test_attached_units_cannot_activate_separately(client):
    """Test that attached heroes cannot be activated separately."""
    # Create game and import units with attachments
    resp = await client.post(
        "/api/games",
        json={"name": "AttachTest", "player_name": "Host", "player_color": "#111111"},
    )
    code = resp.json()["code"]
    host_id = resp.json()["players"][0]["id"]
    
    await client.post(
        f"/api/games/{code}/join",
        json={"player_name": "Guest", "player_color": "#222222"},
    )
    
    # Import units with attachment relationship
    fake_units = [
        {
            "name": "Parent Unit",
            "quality": 4,
            "defense": 4,
            "size": 5,
            "cost": 200,
            "rules": [],
            "selectedUpgrades": [],
            "id": "u1",
            "selectionId": "s1",
        },
        {
            "name": "Attached Hero",
            "quality": 3,
            "defense": 3,
            "size": 1,
            "cost": 50,
            "rules": [{"name": "Hero"}],
            "selectedUpgrades": [],
            "id": "u2",
            "selectionId": "s2",
            "joinToUnit": "s1",  # Attached to parent
        }
    ]
    
    async def fake_get(url, *args, **kwargs):
        class FakeResponse:
            status_code = 200
            def raise_for_status(self): ...
            def json(self):
                return {"units": fake_units}
        return FakeResponse()
    
    with patch("app.army_forge.import_service.httpx.AsyncClient.get", new=AsyncMock(side_effect=fake_get)):
        await client.post(
            f"/api/proxy/import-army/{code}",
            json={"army_forge_url": "https://army-forge.onepagerules.com/share?id=FAKE12345", "player_id": host_id},
        )
    
    # Get the units
    game_resp = await client.get(f"/api/games/{code}")
    units = game_resp.json().get("units", [])
    
    # Find the attached hero
    attached_hero = next((u for u in units if u.get("name") == "Attached Hero"), None)
    assert attached_hero is not None
    assert attached_hero.get("attached_to_unit_id") is not None
    
    # Try to activate the attached hero directly - should fail
    resp_activate = await client.patch(
        f"/api/games/{code}/units/{attached_hero['id']}",
        json={"activated_this_round": True},
    )
    assert resp_activate.status_code in (400, 422)
    assert "attached" in resp_activate.json().get("detail", "").lower()


@pytest.mark.asyncio
async def test_activating_parent_activates_attached_heroes(client):
    """Test that activating a parent unit also activates attached heroes."""
    # Create game and import units with attachments
    resp = await client.post(
        "/api/games",
        json={"name": "ActivateTest", "player_name": "Host", "player_color": "#111111"},
    )
    code = resp.json()["code"]
    host_id = resp.json()["players"][0]["id"]
    
    await client.post(
        f"/api/games/{code}/join",
        json={"player_name": "Guest", "player_color": "#222222"},
    )
    
    # Start game
    await client.post(f"/api/games/{code}/start")
    
    # Import units with attachment
    fake_units = [
        {
            "name": "Parent Squad",
            "quality": 4,
            "defense": 4,
            "size": 5,
            "cost": 200,
            "rules": [],
            "selectedUpgrades": [],
            "id": "u1",
            "selectionId": "s1",
        },
        {
            "name": "Hero",
            "quality": 3,
            "defense": 3,
            "size": 1,
            "cost": 50,
            "rules": [{"name": "Hero"}],
            "selectedUpgrades": [],
            "id": "u2",
            "selectionId": "s2",
            "joinToUnit": "s1",
        }
    ]
    
    async def fake_get(url, *args, **kwargs):
        class FakeResponse:
            status_code = 200
            def raise_for_status(self): ...
            def json(self):
                return {"units": fake_units}
        return FakeResponse()
    
    with patch("app.army_forge.import_service.httpx.AsyncClient.get", new=AsyncMock(side_effect=fake_get)):
        await client.post(
            f"/api/proxy/import-army/{code}",
            json={"army_forge_url": "https://army-forge.onepagerules.com/share?id=FAKE12345", "player_id": host_id},
        )
    
    # Get the units
    game_resp = await client.get(f"/api/games/{code}")
    units = game_resp.json().get("units", [])
    
    parent_unit = next((u for u in units if u.get("name") == "Parent Squad"), None)
    hero_unit = next((u for u in units if u.get("name") == "Hero"), None)
    
    assert parent_unit is not None
    assert hero_unit is not None
    assert hero_unit.get("attached_to_unit_id") == parent_unit["id"]
    
    # Activate the parent unit
    resp_activate = await client.patch(
        f"/api/games/{code}/units/{parent_unit['id']}",
        json={"activated_this_round": True},
    )
    assert resp_activate.status_code == 200
    
    # Check events - should have activation events for both
    resp_events = await client.get(f"/api/games/{code}/events")
    events = resp_events.json()
    activation_events = [e for e in events if e["event_type"] == "unit_activated"]
    
    # Should have at least 2 activation events (parent + hero)
    assert len(activation_events) >= 2
    activated_unit_ids = {e.get("target_unit_id") for e in activation_events}
    assert parent_unit["id"] in activated_unit_ids
    assert hero_unit["id"] in activated_unit_ids
    
    # Verify hero is also activated
    game_resp2 = await client.get(f"/api/games/{code}")
    units2 = game_resp2.json().get("units", [])
    hero_unit2 = next((u for u in units2 if u.get("id") == hero_unit["id"]), None)
    assert hero_unit2 is not None
    assert hero_unit2.get("state", {}).get("activated_this_round") is True


@pytest.mark.asyncio
async def test_manual_detachment(client):
    """Test manual detachment of attached heroes."""
    # Create game and import units with attachments
    resp = await client.post(
        "/api/games",
        json={"name": "DetachTest", "player_name": "Host", "player_color": "#111111"},
    )
    code = resp.json()["code"]
    host_id = resp.json()["players"][0]["id"]
    
    await client.post(
        f"/api/games/{code}/join",
        json={"player_name": "Guest", "player_color": "#222222"},
    )
    
    # Import units with attachment
    fake_units = [
        {
            "name": "Parent",
            "quality": 4,
            "defense": 4,
            "size": 5,
            "cost": 200,
            "rules": [],
            "selectedUpgrades": [],
            "id": "u1",
            "selectionId": "s1",
        },
        {
            "name": "Hero",
            "quality": 3,
            "defense": 3,
            "size": 1,
            "cost": 50,
            "rules": [{"name": "Hero"}],
            "selectedUpgrades": [],
            "id": "u2",
            "selectionId": "s2",
            "joinToUnit": "s1",
        }
    ]
    
    async def fake_get(url, *args, **kwargs):
        class FakeResponse:
            status_code = 200
            def raise_for_status(self): ...
            def json(self):
                return {"units": fake_units}
        return FakeResponse()
    
    with patch("app.army_forge.import_service.httpx.AsyncClient.get", new=AsyncMock(side_effect=fake_get)):
        await client.post(
            f"/api/proxy/import-army/{code}",
            json={"army_forge_url": "https://army-forge.onepagerules.com/share?id=FAKE12345", "player_id": host_id},
        )
    
    # Get the hero unit
    game_resp = await client.get(f"/api/games/{code}")
    units = game_resp.json().get("units", [])
    hero_unit = next((u for u in units if u.get("name") == "Hero"), None)
    assert hero_unit is not None
    assert hero_unit.get("attached_to_unit_id") is not None
    
    # Detach the hero
    resp_detach = await client.patch(
        f"/api/games/{code}/units/{hero_unit['id']}/detach",
    )
    assert resp_detach.status_code == 200
    
    # Verify hero is detached
    game_resp2 = await client.get(f"/api/games/{code}")
    units2 = game_resp2.json().get("units", [])
    hero_unit2 = next((u for u in units2 if u.get("id") == hero_unit["id"]), None)
    assert hero_unit2 is not None
    assert hero_unit2.get("attached_to_unit_id") is None
    
    # Check for detachment event
    resp_events = await client.get(f"/api/games/{code}/events")
    events = resp_events.json()
    detach_events = [e for e in events if e["event_type"] == "unit_detached"]
    assert len(detach_events) > 0
    assert any(e.get("target_unit_id") == hero_unit["id"] for e in detach_events)


@pytest.mark.asyncio
async def test_automatic_detachment_on_destroy(client):
    """Test that attached heroes are automatically detached when parent is destroyed."""
    # Create game and import units with attachments
    resp = await client.post(
        "/api/games",
        json={"name": "DestroyTest", "player_name": "Host", "player_color": "#111111"},
    )
    code = resp.json()["code"]
    host_id = resp.json()["players"][0]["id"]
    
    await client.post(
        f"/api/games/{code}/join",
        json={"player_name": "Guest", "player_color": "#222222"},
    )
    
    # Import units with attachment
    fake_units = [
        {
            "name": "Parent Squad",
            "quality": 4,
            "defense": 4,
            "size": 5,
            "cost": 200,
            "rules": [],
            "selectedUpgrades": [],
            "id": "u1",
            "selectionId": "s1",
        },
        {
            "name": "Elite Hero",
            "quality": 3,
            "defense": 3,
            "size": 1,
            "cost": 50,
            "rules": [{"name": "Hero"}],
            "selectedUpgrades": [],
            "id": "u2",
            "selectionId": "s2",
            "joinToUnit": "s1",
        }
    ]
    
    async def fake_get(url, *args, **kwargs):
        class FakeResponse:
            status_code = 200
            def raise_for_status(self): ...
            def json(self):
                return {"units": fake_units}
        return FakeResponse()
    
    with patch("app.army_forge.import_service.httpx.AsyncClient.get", new=AsyncMock(side_effect=fake_get)):
        await client.post(
            f"/api/proxy/import-army/{code}",
            json={"army_forge_url": "https://army-forge.onepagerules.com/share?id=FAKE12345", "player_id": host_id},
        )
    
    # Get the units
    game_resp = await client.get(f"/api/games/{code}")
    units = game_resp.json().get("units", [])
    
    parent_unit = next((u for u in units if u.get("name") == "Parent Squad"), None)
    hero_unit = next((u for u in units if u.get("name") == "Elite Hero"), None)
    
    assert parent_unit is not None
    assert hero_unit is not None
    assert hero_unit.get("attached_to_unit_id") == parent_unit["id"]
    
    # Destroy the parent unit
    resp_destroy = await client.patch(
        f"/api/games/{code}/units/{parent_unit['id']}",
        json={"deployment_status": "destroyed"},
    )
    assert resp_destroy.status_code == 200
    
    # Verify hero is detached
    game_resp2 = await client.get(f"/api/games/{code}")
    units2 = game_resp2.json().get("units", [])
    hero_unit2 = next((u for u in units2 if u.get("id") == hero_unit["id"]), None)
    assert hero_unit2 is not None
    assert hero_unit2.get("attached_to_unit_id") is None
    
    # Check for detachment and destroy events
    resp_events = await client.get(f"/api/games/{code}/events")
    events = resp_events.json()
    destroy_events = [e for e in events if e["event_type"] == "unit_destroyed"]
    detach_events = [e for e in events if e["event_type"] == "unit_detached"]
    
    assert len(destroy_events) > 0
    assert any(e.get("target_unit_id") == parent_unit["id"] for e in destroy_events)
    assert len(detach_events) > 0
    assert any(e.get("target_unit_id") == hero_unit["id"] for e in detach_events)


@pytest.mark.asyncio
async def test_shaken_status_preserved_on_detachment(client):
    """Test that shaken status is preserved when a shaken parent unit is destroyed."""
    # Create game and import units with attachments
    resp = await client.post(
        "/api/games",
        json={"name": "ShakenDetachTest", "player_name": "Host", "player_color": "#111111"},
    )
    code = resp.json()["code"]
    host_id = resp.json()["players"][0]["id"]
    
    await client.post(
        f"/api/games/{code}/join",
        json={"player_name": "Guest", "player_color": "#222222"},
    )
    
    # Import units with attachment
    fake_units = [
        {
            "name": "Shaken Parent",
            "quality": 4,
            "defense": 4,
            "size": 5,
            "cost": 200,
            "rules": [],
            "selectedUpgrades": [],
            "id": "u1",
            "selectionId": "s1",
        },
        {
            "name": "Surviving Hero",
            "quality": 3,
            "defense": 3,
            "size": 1,
            "cost": 50,
            "rules": [],
            "selectedUpgrades": [],
            "id": "u2",
            "selectionId": "s2",
            "joinToUnit": "s1",  # Attached to parent
        },
    ]
    
    async def fake_get(url, *args, **kwargs):
        class FakeResponse:
            status_code = 200
            def raise_for_status(self): ...
            def json(self):
                return {"units": fake_units}
        return FakeResponse()
    
    with patch("app.army_forge.import_service.httpx.AsyncClient.get", new=AsyncMock(side_effect=fake_get)):
        await client.post(
            f"/api/proxy/import-army/{code}",
            json={"army_forge_url": "https://army-forge.onepagerules.com/share?id=FAKE12345", "player_id": host_id},
        )
    
    # Get the units
    game_resp = await client.get(f"/api/games/{code}")
    units = game_resp.json().get("units", [])
    parent_unit = next((u for u in units if u.get("name") == "Shaken Parent"), None)
    hero_unit = next((u for u in units if u.get("name") == "Surviving Hero"), None)
    
    assert parent_unit is not None
    assert hero_unit is not None
    assert hero_unit.get("attached_to_unit_id") == parent_unit["id"]
    
    # Set parent unit to shaken
    resp_shaken = await client.patch(
        f"/api/games/{code}/units/{parent_unit['id']}",
        json={"is_shaken": True},
    )
    assert resp_shaken.status_code == 200
    
    # Verify hero is also shaken (synced from parent)
    game_resp_shaken = await client.get(f"/api/games/{code}")
    units_shaken = game_resp_shaken.json().get("units", [])
    hero_unit_shaken = next((u for u in units_shaken if u.get("id") == hero_unit["id"]), None)
    assert hero_unit_shaken is not None
    assert hero_unit_shaken["state"]["is_shaken"] is True
    
    # Destroy the shaken parent unit
    resp_destroy = await client.patch(
        f"/api/games/{code}/units/{parent_unit['id']}",
        json={"deployment_status": "destroyed"},
    )
    assert resp_destroy.status_code == 200
    
    # Verify hero is detached but still shaken
    game_resp2 = await client.get(f"/api/games/{code}")
    units2 = game_resp2.json().get("units", [])
    hero_unit2 = next((u for u in units2 if u.get("id") == hero_unit["id"]), None)
    assert hero_unit2 is not None
    assert hero_unit2.get("attached_to_unit_id") is None  # Detached
    assert hero_unit2["state"]["is_shaken"] is True  # Still shaken
    
    # Check for events: shaken status preserved on hero
    resp_events = await client.get(f"/api/games/{code}/events")
    events = resp_events.json()
    shaken_events = [e for e in events if e["event_type"] == "status_shaken" and e.get("target_unit_id") == hero_unit["id"]]
    # Should have shaken event from when parent was shaken, and possibly one from detachment
    assert len(shaken_events) > 0


@pytest.mark.asyncio
async def test_shaken_unshaken_logging(client):
    """Test that shaken/unshaken state changes are logged."""
    # Create game and import a unit
    resp = await client.post(
        "/api/games",
        json={"name": "ShakenTest", "player_name": "Host", "player_color": "#111111"},
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
    
    with patch("app.army_forge.import_service.httpx.AsyncClient.get", new=AsyncMock(side_effect=fake_get)):
        await client.post(
            f"/api/proxy/import-army/{code}",
            json={"army_forge_url": "https://army-forge.onepagerules.com/share?id=FAKE12345", "player_id": host_id},
        )
    
    # Get the unit
    game_resp = await client.get(f"/api/games/{code}")
    units = game_resp.json().get("units", [])
    unit_id = units[0]["id"]
    
    # Set unit to shaken
    resp_shaken = await client.patch(
        f"/api/games/{code}/units/{unit_id}",
        json={"is_shaken": True},
    )
    assert resp_shaken.status_code == 200
    
    # Check for shaken event
    resp_events = await client.get(f"/api/games/{code}/events")
    events = resp_events.json()
    shaken_events = [e for e in events if e["event_type"] == "status_shaken"]
    assert len(shaken_events) > 0
    assert any(e.get("target_unit_id") == unit_id for e in shaken_events)
    
    # Clear shaken status
    resp_unshaken = await client.patch(
        f"/api/games/{code}/units/{unit_id}",
        json={"is_shaken": False},
    )
    assert resp_unshaken.status_code == 200
    
    # Check for shaken cleared event
    resp_events2 = await client.get(f"/api/games/{code}/events")
    events2 = resp_events2.json()
    cleared_events = [e for e in events2 if e["event_type"] == "status_shaken_cleared"]
    assert len(cleared_events) > 0
    assert any(e.get("target_unit_id") == unit_id for e in cleared_events)


@pytest.mark.asyncio
async def test_clear_all_units_success(client):
    """Test clearing all units for a player."""
    # Create game and join second player
    resp = await client.post(
        "/api/games",
        json={"name": "ClearTest", "player_name": "Host", "player_color": "#111111"},
    )
    code = resp.json()["code"]
    host_id = resp.json()["players"][0]["id"]
    
    await client.post(
        f"/api/games/{code}/join",
        json={"player_name": "Guest", "player_color": "#222222"},
    )
    
    # Import units
    fake_units = [
        {
            "name": "Unit 1",
            "quality": 4,
            "defense": 4,
            "size": 1,
            "cost": 100,
            "rules": [],
            "selectedUpgrades": [],
            "id": "u1",
            "selectionId": "s1",
        },
        {
            "name": "Unit 2",
            "quality": 4,
            "defense": 4,
            "size": 1,
            "cost": 150,
            "rules": [],
            "selectedUpgrades": [],
            "id": "u2",
            "selectionId": "s2",
        }
    ]
    
    async def fake_get(url, *args, **kwargs):
        class FakeResponse:
            status_code = 200
            def raise_for_status(self): ...
            def json(self):
                return {"units": fake_units}
        return FakeResponse()
    
    with patch("app.army_forge.import_service.httpx.AsyncClient.get", new=AsyncMock(side_effect=fake_get)), patch(
        "app.army_forge.import_service.broadcast_to_game", new=AsyncMock()
    ):
        await client.post(
            f"/api/proxy/import-army/{code}",
            json={"army_forge_url": "https://army-forge.onepagerules.com/share?id=FAKE12345", "player_id": host_id},
        )
    
    # Verify units exist
    game_resp = await client.get(f"/api/games/{code}")
    units = game_resp.json().get("units", [])
    assert len(units) == 2
    
    # Clear all units
    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()) as mock_broadcast:
        resp_clear = await client.delete(f"/api/games/{code}/players/{host_id}/units")
        assert resp_clear.status_code == 200
        data = resp_clear.json()
        assert data["success"] is True
        assert data["units_cleared"] == 2
        assert "2 units" in data["message"]
        
        # Verify broadcast was called
        mock_broadcast.assert_awaited_once()
        args, kwargs = mock_broadcast.await_args
        assert args[0] == code
        assert args[1]["type"] == "state_update"
        assert args[1]["data"]["reason"] == "units_cleared"
    
    # Verify units are gone
    game_resp2 = await client.get(f"/api/games/{code}")
    units2 = game_resp2.json().get("units", [])
    assert len(units2) == 0
    
    # Verify player stats reset
    players = game_resp2.json().get("players", [])
    host_player = next((p for p in players if p["id"] == host_id), None)
    assert host_player is not None
    assert host_player["starting_unit_count"] == 0
    assert host_player["starting_points"] == 0
    assert host_player["army_name"] is None
    
    # Verify event was created
    resp_events = await client.get(f"/api/games/{code}/events")
    events = resp_events.json()
    clear_events = [e for e in events if "cleared all units" in e.get("description", "").lower()]
    assert len(clear_events) > 0
    clear_event = clear_events[0]
    assert clear_event["event_type"] == "custom"
    assert clear_event["details"]["units_cleared"] == 2
    assert clear_event["details"]["points_cleared"] == 250


@pytest.mark.asyncio
async def test_clear_all_units_blocked_when_game_started(client):
    """Test that clearing units is blocked when game has started."""
    # Create game, join, and add units
    resp = await client.post(
        "/api/games",
        json={"name": "ClearBlockedTest", "player_name": "Host", "player_color": "#111111"},
    )
    code = resp.json()["code"]
    host_id = resp.json()["players"][0]["id"]
    guest_id = (await client.post(
        f"/api/games/{code}/join",
        json={"player_name": "Guest", "player_color": "#222222"},
    )).json()["your_player_id"]
    
    # Add units for both players (required to start game)
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
    
    with patch("app.army_forge.import_service.httpx.AsyncClient.get", new=AsyncMock(side_effect=fake_get)), patch(
        "app.army_forge.import_service.broadcast_to_game", new=AsyncMock()
    ):
        await client.post(
            f"/api/proxy/import-army/{code}",
            json={"army_forge_url": "https://army-forge.onepagerules.com/share?id=FAKE12345", "player_id": host_id},
        )
        await client.post(
            f"/api/proxy/import-army/{code}",
            json={"army_forge_url": "https://army-forge.onepagerules.com/share?id=FAKE67890", "player_id": guest_id},
        )
    
    # Start game
    await client.post(f"/api/games/{code}/start")
    
    # Try to clear units - should fail
    resp_clear = await client.delete(f"/api/games/{code}/players/{host_id}/units")
    assert resp_clear.status_code in (400, 422)
    assert "lobby" in resp_clear.json().get("detail", "").lower()


@pytest.mark.asyncio
async def test_clear_all_units_with_no_units(client):
    """Test clearing units when player has no units."""
    # Create game and join second player
    resp = await client.post(
        "/api/games",
        json={"name": "ClearEmptyTest", "player_name": "Host", "player_color": "#111111"},
    )
    code = resp.json()["code"]
    host_id = resp.json()["players"][0]["id"]
    
    await client.post(
        f"/api/games/{code}/join",
        json={"player_name": "Guest", "player_color": "#222222"},
    )
    
    # Clear units when player has none
    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        resp_clear = await client.delete(f"/api/games/{code}/players/{host_id}/units")
        assert resp_clear.status_code == 200
        data = resp_clear.json()
        assert data["success"] is True
        assert data["units_cleared"] == 0


@pytest.mark.asyncio
async def test_manual_unit_with_loadout_rules_upgrades(client):
    """Create unit with loadout, rules, and upgrades; GET game returns them."""
    resp = await client.post(
        "/api/games",
        json={"name": "UnitDetailsTest", "player_name": "Host", "player_color": "#111111"},
    )
    code = resp.json()["code"]
    host_id = resp.json()["players"][0]["id"]
    create_payload = {
        "player_id": host_id,
        "name": "Veteran Squad",
        "quality": 3,
        "defense": 4,
        "size": 5,
        "tough": 1,
        "cost": 250,
        "rules": [{"name": "Tough", "rating": 2}, {"name": "Hero"}],
        "loadout": [
            {"name": "Rifle", "label": "Heavy Rifle", "range": 24, "attacks": 1},
            {"name": "Plasma", "range": 12, "attacks": 1, "specialRules": [{"name": "AP", "rating": 2}]},
        ],
        "upgrades": [{"name": "Veteran"}, {"name": "Weapon Upgrade", "content": [{"name": "Plasma"}]}],
    }
    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        resp_unit = await client.post(f"/api/games/{code}/units/manual", json=create_payload)
    assert resp_unit.status_code == 201
    data = resp_unit.json()
    assert data["name"] == "Veteran Squad"
    assert data.get("rules") == create_payload["rules"]
    assert data.get("loadout") == create_payload["loadout"]
    assert data.get("upgrades") == create_payload["upgrades"]
    # GET game includes unit with same data
    resp_game = await client.get(f"/api/games/{code}")
    assert resp_game.status_code == 200
    units = resp_game.json().get("units", [])
    unit = next((u for u in units if u["name"] == "Veteran Squad"), None)
    assert unit is not None
    assert unit.get("upgrades") == create_payload["upgrades"]


@pytest.mark.asyncio
async def test_delete_unit_in_lobby(client):
    """Deleting a unit in lobby removes it and updates player stats."""
    code, host_id, unit_id = await create_game_with_manual_unit(client)

    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        resp = await client.delete(f"/api/games/{code}/units/{unit_id}")
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    game = (await client.get(f"/api/games/{code}")).json()
    host = next(p for p in game["players"] if p["id"] == host_id)
    assert host["starting_unit_count"] == 0
    assert host["starting_points"] == 0

    units = game.get("units", [])
    assert all(u["id"] != unit_id for u in units)


@pytest.mark.asyncio
async def test_delete_unit_not_found(client):
    """Deleting a non-existent unit returns 404."""
    resp = await client.post(
        "/api/games",
        json={"name": "DelNF", "player_name": "Host", "player_color": "#111111"},
    )
    code = resp.json()["code"]
    fake_id = str(uuid.uuid4())

    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        resp = await client.delete(f"/api/games/{code}/units/{fake_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_unit_after_start_rejected(client):
    """Deleting a unit after the game has started is rejected."""
    code, host_id, unit_id = await create_game_with_manual_unit(client)

    join_resp = await client.post(
        f"/api/games/{code}/join",
        json={"player_name": "Guest", "player_color": "#222222"},
    )
    assert join_resp.status_code == 201
    guest_id = next(p["id"] for p in join_resp.json()["players"] if p["name"] == "Guest")

    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        await client.post(f"/api/games/{code}/units/manual", json={
            "player_id": guest_id, "name": "Guest Squad",
            "quality": 4, "defense": 4, "size": 1, "tough": 1, "cost": 50,
        })

    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        start_resp = await client.post(f"/api/games/{code}/start")
    assert start_resp.status_code == 201

    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        resp = await client.delete(f"/api/games/{code}/units/{unit_id}")
    assert resp.status_code in (400, 422, 500)


@pytest.mark.asyncio
async def test_rename_unit_in_lobby(client):
    """Renaming a unit in lobby sets custom_name and returns updated unit."""
    code, host_id, unit_id = await create_game_with_manual_unit(client)

    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        resp = await client.patch(
            f"/api/games/{code}/units/{unit_id}/profile",
            json={"custom_name": "Alpha Squad"},
        )
    assert resp.status_code == 200
    assert resp.json()["custom_name"] == "Alpha Squad"


@pytest.mark.asyncio
async def test_rename_unit_empty_clears_name(client):
    """Sending empty string for custom_name clears it back to None."""
    code, host_id, unit_id = await create_game_with_manual_unit(client)

    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        await client.patch(
            f"/api/games/{code}/units/{unit_id}/profile",
            json={"custom_name": "Temp Name"},
        )
        resp = await client.patch(
            f"/api/games/{code}/units/{unit_id}/profile",
            json={"custom_name": ""},
        )
    assert resp.status_code == 200
    assert resp.json()["custom_name"] is None


@pytest.mark.asyncio
async def test_rename_unit_after_start_rejected(client):
    """Renaming a unit after the game has started is rejected."""
    code, host_id, unit_id = await create_game_with_manual_unit(client)

    join_resp = await client.post(
        f"/api/games/{code}/join",
        json={"player_name": "Guest", "player_color": "#222222"},
    )
    assert join_resp.status_code == 201
    guest_id = next(p["id"] for p in join_resp.json()["players"] if p["name"] == "Guest")

    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        await client.post(f"/api/games/{code}/units/manual", json={
            "player_id": guest_id, "name": "Guest Squad",
            "quality": 4, "defense": 4, "size": 1, "tough": 1, "cost": 50,
        })

    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        await client.post(f"/api/games/{code}/start")

    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        resp = await client.patch(
            f"/api/games/{code}/units/{unit_id}/profile",
            json={"custom_name": "Nope"},
        )
    assert resp.status_code in (400, 422, 500)


@pytest.mark.asyncio
async def test_transport_destroyed_auto_disembarks_passengers(client):
    """When a transport is destroyed, embarked units are auto-disembarked and shaken."""
    resp = await client.post(
        "/api/games",
        json={"name": "TransportTest", "player_name": "Host", "player_color": "#111111"},
    )
    code = resp.json()["code"]
    host_id = resp.json()["players"][0]["id"]

    # Create a transport
    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        t_resp = await client.post(f"/api/games/{code}/units/manual", json={
            "player_id": host_id, "name": "APC", "quality": 4, "defense": 3,
            "size": 1, "tough": 3, "cost": 150,
            "is_transport": True, "transport_capacity": 5,
        })
    assert t_resp.status_code == 201
    transport_id = t_resp.json()["id"]

    # Create a passenger unit
    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        p_resp = await client.post(f"/api/games/{code}/units/manual", json={
            "player_id": host_id, "name": "Infantry", "quality": 4, "defense": 4,
            "size": 5, "tough": 1, "cost": 100,
        })
    assert p_resp.status_code == 201
    passenger_id = p_resp.json()["id"]

    # Add a second player and start the game
    join_resp = await client.post(
        f"/api/games/{code}/join",
        json={"player_name": "Guest", "player_color": "#222222"},
    )
    assert join_resp.status_code == 201
    guest_id = join_resp.json()["your_player_id"]

    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        g_resp = await client.post(f"/api/games/{code}/units/manual", json={
            "player_id": guest_id, "name": "Enemy", "quality": 4, "defense": 4,
            "size": 3, "tough": 1, "cost": 100,
        })
    assert g_resp.status_code == 201

    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        await client.post(f"/api/games/{code}/start")

    # Embark the infantry into the transport
    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        embark_resp = await client.patch(
            f"/api/games/{code}/units/{passenger_id}",
            json={"transport_id": transport_id},
        )
    assert embark_resp.status_code == 200
    assert embark_resp.json()["state"]["deployment_status"] == "embarked"

    # Destroy the transport
    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        destroy_resp = await client.patch(
            f"/api/games/{code}/units/{transport_id}",
            json={"deployment_status": "destroyed"},
        )
    assert destroy_resp.status_code == 200

    # Verify the passenger was auto-disembarked and shaken
    game_resp = await client.get(f"/api/games/{code}")
    assert game_resp.status_code == 200
    passenger_data = next(
        u for u in game_resp.json()["units"] if u["id"] == passenger_id
    )
    assert passenger_data["state"]["deployment_status"] == "deployed"
    assert passenger_data["state"]["transport_id"] is None
    assert passenger_data["state"]["is_shaken"] is True


@pytest.mark.asyncio
async def test_combined_unit_merged_not_attached(client):
    """Combined (doubled) squads should be merged into one unit, not treated as hero attachments."""
    resp = await client.post(
        "/api/games",
        json={"name": "CombinedTest", "player_name": "Host", "player_color": "#111111"},
    )
    code = resp.json()["code"]
    join = await client.post(
        f"/api/games/{code}/join",
        json={"player_name": "Guest", "player_color": "#222222"},
    )
    guest_id = join.json()["your_player_id"]

    fake_units = [
        {
            "name": "Battle Brothers",
            "quality": 4,
            "defense": 4,
            "size": 5,
            "cost": 100,
            "rules": [],
            "selectedUpgrades": [],
            "loadout": [],
            "id": "u1",
            "selectionId": "sel_parent",
            "armyId": "army1",
        },
        {
            "name": "Battle Brothers",
            "quality": 4,
            "defense": 4,
            "size": 5,
            "cost": 100,
            "rules": [],
            "selectedUpgrades": [],
            "loadout": [],
            "id": "u2",
            "selectionId": "sel_combined",
            "joinToUnit": "sel_parent",
            "combined": True,
            "armyId": "army1",
        },
        {
            "name": "Captain",
            "quality": 3,
            "defense": 4,
            "size": 1,
            "cost": 60,
            "rules": [{"name": "Hero"}, {"name": "Tough", "rating": "3"}],
            "selectedUpgrades": [],
            "loadout": [],
            "id": "u3",
            "selectionId": "sel_hero",
            "joinToUnit": "sel_parent",
            "armyId": "army1",
        },
    ]

    async def fake_get(url, *args, **kwargs):
        class FakeResponse:
            status_code = 200
            def raise_for_status(self): ...
            def json(self):
                return {"units": fake_units, "gameSystem": "gf"}
        return FakeResponse()

    with patch("app.army_forge.import_service.httpx.AsyncClient.get", new=AsyncMock(side_effect=fake_get)), \
         patch("app.army_forge.import_service.broadcast_to_game", new=AsyncMock()):
        resp_import = await client.post(
            f"/api/proxy/import-army/{code}",
            json={
                "army_forge_url": "https://army-forge.onepagerules.com/share?id=FAKE_COMBINED",
                "player_id": guest_id,
            },
        )
    assert resp_import.status_code in (200, 201)
    data = resp_import.json()
    # 3 raw units, but combined unit merged → 2 actual units imported
    assert data["units_imported"] == 2

    game_resp = await client.get(f"/api/games/{code}")
    assert game_resp.status_code == 200
    units = game_resp.json()["units"]

    # Only 2 units should exist: the merged parent and the hero
    assert len(units) == 2

    parent = next(u for u in units if u["name"] == "Battle Brothers")
    hero = next(u for u in units if u["name"] == "Captain")

    # Combined unit size should be 5 + 5 = 10
    assert parent["size"] == 10
    assert parent["state"]["models_remaining"] == 10

    # Hero should be attached to the parent
    assert hero["is_hero"] is True
    assert hero["attached_to_unit_id"] == parent["id"]

    # Parent should NOT have attached_to_unit_id
    assert parent["attached_to_unit_id"] is None
