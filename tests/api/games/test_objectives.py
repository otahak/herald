"""Tests for objectives API."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_create_and_update_objectives(client):
    resp = await client.post(
        "/api/games",
        json={"name": "ObjGame", "player_name": "Host", "player_color": "#111111"},
    )
    code = resp.json()["code"]
    host_id = resp.json()["players"][0]["id"]
    await client.post(
        f"/api/games/{code}/join",
        json={"player_name": "Guest", "player_color": "#222222"},
    )

    r1 = await client.post(f"/api/games/{code}/objectives", json={"count": 3})
    assert r1.status_code == 201
    objs = r1.json()
    assert len(objs) == 3
    oid = objs[0]["id"]

    r_dup = await client.post(f"/api/games/{code}/objectives", json={"count": 3})
    assert r_dup.status_code in (400, 422, 500)

    fake = str(uuid.uuid4())
    r_nf = await client.patch(
        f"/api/games/{code}/objectives/{fake}",
        json={"status": "neutral"},
    )
    assert r_nf.status_code == 404

    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        r_seize = await client.patch(
            f"/api/games/{code}/objectives/{oid}",
            json={"status": "seized", "controlled_by_id": host_id},
        )
    assert r_seize.status_code == 200

    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        r_contest = await client.patch(
            f"/api/games/{code}/objectives/{oid}",
            json={"status": "contested"},
        )
    assert r_contest.status_code == 200

    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        r_neutral = await client.patch(
            f"/api/games/{code}/objectives/{oid}",
            json={"status": "neutral"},
        )
    assert r_neutral.status_code == 200


@pytest.mark.asyncio
async def test_seized_without_controller_skips_seize_log(client):
    resp = await client.post(
        "/api/games",
        json={"name": "Obj2", "player_name": "Host", "player_color": "#111111"},
    )
    code = resp.json()["code"]
    await client.post(
        f"/api/games/{code}/join",
        json={"player_name": "Guest", "player_color": "#222222"},
    )
    r1 = await client.post(f"/api/games/{code}/objectives", json={"count": 3})
    oid = r1.json()[0]["id"]
    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        r = await client.patch(
            f"/api/games/{code}/objectives/{oid}",
            json={"status": "seized"},
        )
    assert r.status_code == 200
