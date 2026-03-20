import uuid
from unittest.mock import AsyncMock, patch

import pytest

@pytest.mark.asyncio
async def test_solo_create_with_opponent_name(client):
    """Create solo game with opponent_name sets the opponent's initial name."""
    resp = await client.post(
        "/api/games",
        json={
            "name": "SoloCustom",
            "player_name": "Host",
            "player_color": "#111111",
            "is_solo": True,
            "opponent_name": "The Enemy",
        },
    )
    assert resp.status_code == 201
    players = resp.json()["players"]
    assert len(players) == 2
    opponent = next(p for p in players if not p.get("is_host"))
    assert opponent["name"] == "The Enemy"


@pytest.mark.asyncio
async def test_solo_rename_opponent(client):
    """In solo mode, opponent player name can be updated via PATCH."""
    resp = await client.post(
        "/api/games",
        json={"name": "SoloRename", "player_name": "Host", "player_color": "#111111", "is_solo": True},
    )
    assert resp.status_code == 201
    code = resp.json()["code"]
    players = resp.json()["players"]
    assert len(players) == 2
    opponent = next(p for p in players if not p.get("is_host"))
    assert opponent["name"] == "Opponent"
    opponent_id = opponent["id"]
    patch_resp = await client.patch(
        f"/api/games/{code}/players/{opponent_id}",
        json={"name": "The Enemy"},
    )
    assert patch_resp.status_code == 200
    data = patch_resp.json()
    assert data["name"] == "The Enemy"
    get_resp = await client.get(f"/api/games/{code}")
    assert get_resp.status_code == 200
    updated_players = get_resp.json().get("players", [])
    assert len(updated_players) == 2
    opponent_after = next((p for p in updated_players if not p.get("is_host")), None)
    assert opponent_after is not None
    assert opponent_after["name"] == "The Enemy"


@pytest.mark.asyncio
async def test_rename_player_only_in_solo(client):
    """PATCH player name returns 400/422 when game is not solo."""
    resp = await client.post(
        "/api/games",
        json={"name": "TwoPlayer", "player_name": "Host", "player_color": "#111111"},
    )
    code = resp.json()["code"]
    host_id = resp.json()["players"][0]["id"]
    await client.post(
        f"/api/games/{code}/join",
        json={"player_name": "Guest", "player_color": "#222222"},
    )
    r = await client.patch(
        f"/api/games/{code}/players/{host_id}",
        json={"name": "NewName"},
    )
    assert r.status_code in (400, 422)
    assert "solo" in r.json().get("detail", "").lower()


@pytest.mark.asyncio
async def test_save_and_load_restores_state(client):
    """Save stores full state; load restores game, players, units, and unit states."""
    resp = await client.post(
        "/api/games",
        json={"name": "SaveLoad", "player_name": "Me", "player_color": "#111111", "is_solo": True},
    )
    assert resp.status_code == 201
    code = resp.json()["code"]
    host_id = resp.json()["players"][0]["id"]
    # Add a unit and advance round
    await client.post(
        f"/api/games/{code}/units/manual",
        json={
            "player_id": host_id,
            "name": "Test Unit",
            "quality": 4,
            "defense": 4,
            "size": 1,
            "tough": 1,
            "cost": 50,
        },
    )
    await client.patch(f"/api/games/{code}/round", json={"delta": 1})
    # Save
    save_resp = await client.post(
        f"/api/games/{code}/save",
        json={"save_name": "Checkpoint", "description": "After round 2"},
    )
    assert save_resp.status_code == 201
    save_id = save_resp.json()["save_id"]
    # Change state (round and unit wound) so we can verify restore
    await client.patch(f"/api/games/{code}/round", json={"delta": 1})
    get_before = await client.get(f"/api/games/{code}")
    units_before = {u["id"]: u for u in get_before.json()["units"]}
    # Apply a wound to the first unit
    unit_id = next(iter(units_before))
    await client.patch(
        f"/api/games/{code}/units/{unit_id}",
        json={"wounds_taken": 1},
    )
    # Load save
    load_resp = await client.post(
        f"/api/games/{code}/load",
        json={"save_id": save_id},
    )
    assert load_resp.status_code == 200
    loaded = load_resp.json()
    # Restored round should be 2 (what we had at save time), not 3
    assert loaded["current_round"] == 2
    # Restored unit should have 0 wounds (saved state), not 1
    assert len(loaded["units"]) == 1
    assert loaded["units"][0]["state"]["wounds_taken"] == 0


@pytest.mark.asyncio
async def test_game_board_redirects_admin_to_observe(client):
    """When an admin visits /game/{code}, they are redirected to /admin/observe/{code}."""
    with patch("app.game.routes.is_admin_authenticated", new=AsyncMock(return_value=True)):
        resp = await client.get("/game/ABCD", follow_redirects=False)
    assert resp.status_code == 302
    assert "admin/observe/ABCD" in resp.headers.get("location", "")


@pytest.mark.asyncio
async def test_game_board_returns_template_for_non_admin(client):
    """When a non-admin visits /game/{code}, they get the game board template (200)."""
    with patch("app.game.routes.is_admin_authenticated", new=AsyncMock(return_value=False)):
        resp = await client.get("/game/XYZZ", follow_redirects=False)
    assert resp.status_code == 200
    assert "game_code" in resp.text or "XYZZ" in resp.text
