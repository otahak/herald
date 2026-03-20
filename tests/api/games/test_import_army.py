import uuid
from unittest.mock import AsyncMock, patch

import pytest

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
    with patch("app.army_forge.import_service.httpx.AsyncClient.get", new=AsyncMock(side_effect=fake_get)), patch(
        "app.army_forge.import_service.broadcast_to_game", new=AsyncMock()
    ):
        resp_import = await client.post(
            f"/api/proxy/import-army/{code}",
            json={"army_forge_url": "https://army-forge.onepagerules.com/share?id=FAKE12345", "player_id": guest_id},
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
async def test_import_army_share_api_fallback_on_tts_500(client):
    """When TTS API returns 500, fall back to share API + army books."""
    resp = await client.post(
        "/api/games",
        json={"name": "ShareFallbackTest", "player_name": "Host", "player_color": "#111111"},
    )
    code = resp.json()["code"]
    host_id = resp.json()["players"][0]["id"]

    share_data = {
        "gameSystem": "gf",
        "units": [
            {
                "id": "unit1",
                "selectionId": "sel1",
                "armyId": "army1",
                "customName": None,
                "joinToUnit": None,
                "combined": False,
                "selectedUpgrades": [],
            },
        ],
    }
    army_book = {
        "name": "Test Faction",
        "versionString": "1.0",
        "units": [
            {
                "id": "unit1",
                "name": "Veteran Squad",
                "quality": 4,
                "defense": 4,
                "size": 5,
                "cost": 100,
                "rules": [{"name": "Tough", "rating": 1}],
                "weapons": [{"name": "Rifle", "range": 24, "attacks": 1, "specialRules": []}],
                "items": [],
            },
        ],
        "spells": [],
        "specialRules": [],
    }

    call_count = 0

    async def fake_get(url, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if "api/tts" in url:
            class TTS500:
                status_code = 500
                text = ""
                def raise_for_status(self):
                    import httpx
                    raise httpx.HTTPStatusError("500", request=None, response=self)
                def json(self):
                    return {}
            return TTS500()
        if "api/share" in url:
            class ShareOK:
                status_code = 200
                def raise_for_status(self): ...
                def json(self):
                    return share_data
            return ShareOK()
        if "api/army-books" in url:
            class BookOK:
                status_code = 200
                def raise_for_status(self): ...
                def json(self):
                    return army_book
            return BookOK()
        raise ValueError(f"Unexpected URL: {url}")

    with patch("app.army_forge.import_service.httpx.AsyncClient.get", new=AsyncMock(side_effect=fake_get)), patch(
        "app.army_forge.import_service.broadcast_to_game", new=AsyncMock()
    ):
        resp_import = await client.post(
            f"/api/proxy/import-army/{code}",
            json={"army_forge_url": "https://army-forge.onepagerules.com/share?id=ASHEMPACT", "player_id": host_id},
        )
    assert resp_import.status_code in (200, 201), resp_import.text
    data = resp_import.json()
    assert data["units_imported"] == 1
    assert "Test Faction" in data["army_name"]

    updated = await client.get(f"/api/games/{code}")
    units = updated.json().get("units", [])
    assert any(u["name"] == "Veteran Squad" for u in units)


@pytest.mark.asyncio
async def test_army_forge_import_accumulates_units(client):
    """Test that Army Forge import adds units instead of replacing them."""
    # Create game and join second player
    resp = await client.post(
        "/api/games",
        json={"name": "AccumulateTest", "player_name": "Host", "player_color": "#111111"},
    )
    code = resp.json()["code"]
    host_id = resp.json()["players"][0]["id"]
    
    await client.post(
        f"/api/games/{code}/join",
        json={"player_name": "Guest", "player_color": "#222222"},
    )
    
    # First import
    fake_units_1 = [
        {
            "name": "First Unit",
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
    
    async def fake_get_1(url, *args, **kwargs):
        class FakeResponse:
            status_code = 200
            def raise_for_status(self): ...
            def json(self):
                return {"units": fake_units_1}
        return FakeResponse()
    
    with patch("app.army_forge.import_service.httpx.AsyncClient.get", new=AsyncMock(side_effect=fake_get_1)), patch(
        "app.army_forge.import_service.broadcast_to_game", new=AsyncMock()
    ):
        resp_import1 = await client.post(
            f"/api/proxy/import-army/{code}",
            json={"army_forge_url": "https://army-forge.onepagerules.com/share?id=FAKE12345", "player_id": host_id},
        )
        assert resp_import1.status_code in (200, 201)
        assert resp_import1.json()["units_imported"] == 1
    
    # Verify first unit is present
    game_resp1 = await client.get(f"/api/games/{code}")
    units1 = game_resp1.json().get("units", [])
    assert len(units1) == 1
    assert any(u["name"] == "First Unit" for u in units1)
    
    # Second import (should accumulate)
    fake_units_2 = [
        {
            "name": "Second Unit",
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
    
    async def fake_get_2(url, *args, **kwargs):
        class FakeResponse:
            status_code = 200
            def raise_for_status(self): ...
            def json(self):
                return {"units": fake_units_2}
        return FakeResponse()
    
    with patch("app.army_forge.import_service.httpx.AsyncClient.get", new=AsyncMock(side_effect=fake_get_2)), patch(
        "app.army_forge.import_service.broadcast_to_game", new=AsyncMock()
    ):
        resp_import2 = await client.post(
            f"/api/proxy/import-army/{code}",
            json={"army_forge_url": "https://army-forge.onepagerules.com/share?id=FAKE67890", "player_id": host_id},
        )
        assert resp_import2.status_code in (200, 201)
        assert resp_import2.json()["units_imported"] == 1
    
    # Verify both units are present (accumulated)
    game_resp2 = await client.get(f"/api/games/{code}")
    units2 = game_resp2.json().get("units", [])
    assert len(units2) == 2
    assert any(u["name"] == "First Unit" for u in units2)
    assert any(u["name"] == "Second Unit" for u in units2)
    
    # Verify player stats accumulated
    players = game_resp2.json().get("players", [])
    host_player = next((p for p in players if p["id"] == host_id), None)
    assert host_player is not None
    assert host_player["starting_unit_count"] == 2
    assert host_player["starting_points"] == 250  # 100 + 150
