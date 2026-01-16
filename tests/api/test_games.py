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
    
    with patch("app.api.proxy.httpx.AsyncClient.get", new=AsyncMock(side_effect=fake_get)):
        await client.post(
            f"/api/proxy/import-army/{code}",
            json={"army_forge_url": "https://army-forge.onepagerules.com/api/tts?id=FAKE", "player_id": host_id},
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
    
    with patch("app.api.proxy.httpx.AsyncClient.get", new=AsyncMock(side_effect=fake_get)):
        await client.post(
            f"/api/proxy/import-army/{code}",
            json={"army_forge_url": "https://army-forge.onepagerules.com/api/tts?id=FAKE", "player_id": host_id},
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
    
    with patch("app.api.proxy.httpx.AsyncClient.get", new=AsyncMock(side_effect=fake_get)):
        await client.post(
            f"/api/proxy/import-army/{code}",
            json={"army_forge_url": "https://army-forge.onepagerules.com/api/tts?id=FAKE", "player_id": host_id},
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
    
    with patch("app.api.proxy.httpx.AsyncClient.get", new=AsyncMock(side_effect=fake_get)):
        await client.post(
            f"/api/proxy/import-army/{code}",
            json={"army_forge_url": "https://army-forge.onepagerules.com/api/tts?id=FAKE", "player_id": host_id},
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
    
    with patch("app.api.proxy.httpx.AsyncClient.get", new=AsyncMock(side_effect=fake_get)):
        await client.post(
            f"/api/proxy/import-army/{code}",
            json={"army_forge_url": "https://army-forge.onepagerules.com/api/tts?id=FAKE", "player_id": host_id},
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
    
    with patch("app.api.proxy.httpx.AsyncClient.get", new=AsyncMock(side_effect=fake_get)):
        await client.post(
            f"/api/proxy/import-army/{code}",
            json={"army_forge_url": "https://army-forge.onepagerules.com/api/tts?id=FAKE", "player_id": host_id},
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

