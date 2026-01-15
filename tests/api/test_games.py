import uuid
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_create_and_join_game(client):
    create_payload = {
        "name": "Test Game",
        "game_system": "gff",
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
        json={"name": "StartCheck", "game_system": "gff", "player_name": "Host", "player_color": "#111111"},
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
        json={"name": "ImportTest", "game_system": "gff", "player_name": "Host", "player_color": "#111111"},
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
        json={"name": "BroadcastTest", "game_system": "gff", "player_name": "Host", "player_color": "#abcdef"},
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

