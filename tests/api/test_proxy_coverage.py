"""Tests for ``ProxyController.get_army_forge_list`` and rate limit on import."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.army_forge.schemas import ImportArmyResponse


@pytest.mark.asyncio
async def test_proxy_get_army_forge_list_success(client):
    fake = {
        "gameSystem": "gf",
        "units": [
            {
                "armyId": "a1",
                "name": "A",
                "id": "1",
                "selectionId": "s1",
                "defense": 4,
                "quality": 4,
                "size": 1,
                "loadout": [],
                "rules": [],
                "cost": 10,
            }
        ],
    }
    with patch("app.api.proxy.httpx.AsyncClient") as acm:
        inst = acm.return_value.__aenter__.return_value
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value=fake)
        inst.get = AsyncMock(return_value=resp)
        r = await client.get("/api/proxy/army-forge/listid12345")
    assert r.status_code == 200
    assert r.json()["units"]


@pytest.mark.asyncio
async def test_proxy_get_army_forge_list_http_error(client):
    req = MagicMock()
    resp = MagicMock()
    resp.status_code = 404
    resp.text = "nope"
    err = httpx.HTTPStatusError("404", request=req, response=resp)
    with patch("app.api.proxy.httpx.AsyncClient") as acm:
        inst = acm.return_value.__aenter__.return_value
        inst.get = AsyncMock(side_effect=err)
        r = await client.get("/api/proxy/army-forge/missing")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_proxy_import_army_delegates_to_service(client):
    resp = await client.post(
        "/api/games",
        json={"name": "Imp", "player_name": "H", "player_color": "#111"},
    )
    code = resp.json()["code"]
    pid = resp.json()["players"][0]["id"]
    with patch(
        "app.api.proxy.import_army_into_game",
        new=AsyncMock(
            return_value=ImportArmyResponse(units_imported=0, army_name="A", total_points=0)
        ),
    ):
        r = await client.post(
            f"/api/proxy/import-army/{code}",
            json={
                "army_forge_url": "https://army-forge.onepagerules.com/share?id=FAKE12345",
                "player_id": str(pid),
            },
        )
    assert r.status_code == 201
    assert r.json()["army_name"] == "A"


@pytest.mark.asyncio
async def test_proxy_import_army_rate_limit(client):
    resp = await client.post(
        "/api/games",
        json={"name": "RL", "player_name": "H", "player_color": "#111"},
    )
    code = resp.json()["code"]
    pid = resp.json()["players"][0]["id"]
    with patch("app.api.proxy.check_rate_limit", return_value=False):
        r = await client.post(
            f"/api/proxy/import-army/{code}",
            json={
                "army_forge_url": "https://army-forge.onepagerules.com/share?id=FAKE12345",
                "player_id": pid,
            },
        )
    assert r.status_code == 429
