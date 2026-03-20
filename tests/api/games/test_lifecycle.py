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
async def test_player_join_broadcasts(client):
    # create game
    resp = await client.post(
        "/api/games",
        json={"name": "BroadcastTest", "player_name": "Host", "player_color": "#abcdef"},
    )
    code = resp.json()["code"]

    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()) as mock_broadcast:
        resp_join = await client.post(
            f"/api/games/{code}/join",
            json={"player_name": "Joiner", "player_color": "#123123"},
        )
        assert resp_join.status_code == 201
        mock_broadcast.assert_awaited_once()
        args, kwargs = mock_broadcast.await_args
        assert args[0] == code
        assert args[1]["type"] == "player_joined"
