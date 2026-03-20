"""Extra branches in army_forge.import_service via /api/proxy/import-army."""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_import_wrong_game_code(client):
    r = await client.post(
        "/api/proxy/import-army/ZZZZZZ",
        json={
            "army_forge_url": "https://army-forge.onepagerules.com/share?id=FAKE12345",
            "player_id": "00000000-0000-0000-0000-000000000001",
        },
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_import_wrong_player_id(client):
    resp = await client.post(
        "/api/games",
        json={"name": "ImpP", "player_name": "H", "player_color": "#111"},
    )
    code = resp.json()["code"]
    r = await client.post(
        f"/api/proxy/import-army/{code}",
        json={
            "army_forge_url": "https://army-forge.onepagerules.com/share?id=FAKE12345",
            "player_id": "00000000-0000-0000-0000-000000000099",
        },
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_import_invalid_list_url(client):
    resp = await client.post(
        "/api/games",
        json={"name": "ImpU", "player_name": "H", "player_color": "#111"},
    )
    code = resp.json()["code"]
    hid = resp.json()["players"][0]["id"]
    r = await client.post(
        f"/api/proxy/import-army/{code}",
        json={
            "army_forge_url": "https://example.com/not-a-list",
            "player_id": hid,
        },
    )
    assert r.status_code in (400, 422)


@pytest.mark.asyncio
async def test_import_unit_loop_validation_error(client):
    resp = await client.post(
        "/api/games",
        json={"name": "ImpX", "player_name": "H", "player_color": "#111"},
    )
    code = resp.json()["code"]
    hid = resp.json()["players"][0]["id"]

    async def fake_get(url, *args, **kwargs):
        class R:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "units": [
                        {
                            "name": "Bad",
                            "quality": 4,
                            "defense": 4,
                            "size": 1,
                            "cost": 1,
                            "rules": [],
                            "selectedUpgrades": [],
                            "id": "u1",
                            "selectionId": "s1",
                        }
                    ]
                }

        return R()

    with patch("app.army_forge.import_service.httpx.AsyncClient.get", new=AsyncMock(side_effect=fake_get)):
        with patch(
            "app.army_forge.import_service.parse_special_rules",
            side_effect=RuntimeError("parse boom"),
        ):
            r = await client.post(
                f"/api/proxy/import-army/{code}",
                json={
                    "army_forge_url": "https://army-forge.onepagerules.com/share?id=FAKE12345",
                    "player_id": hid,
                },
            )
    assert r.status_code in (400, 422)


@pytest.mark.asyncio
async def test_import_combined_missing_parent_listpoints_spells_rules(client):
    resp = await client.post(
        "/api/games",
        json={"name": "ImpRich", "player_name": "H", "player_color": "#111"},
    )
    code = resp.json()["code"]
    hid = resp.json()["players"][0]["id"]

    payload = {
        "listPoints": 250,
        "gameSystem": "gf",
        "units": [
            {
                "name": "Orphan",
                "quality": 4,
                "defense": 4,
                "size": 1,
                "cost": 50,
                "rules": [],
                "selectedUpgrades": [],
                "id": "u1",
                "selectionId": "s1",
                "joinToUnit": "missing-parent",
                "combined": True,
            },
            {
                "name": "Attached",
                "quality": 3,
                "defense": 3,
                "size": 1,
                "cost": 25,
                "rules": [],
                "selectedUpgrades": [],
                "id": "u2",
                "selectionId": "s2",
                "joinToUnit": "missing-parent-2",
            },
            {
                "name": "Notes",
                "quality": 4,
                "defense": 4,
                "size": 1,
                "cost": 10,
                "rules": [],
                "selectedUpgrades": [],
                "id": "u3",
                "selectionId": "s3",
                "notes": "  " + "x" * 600,
            },
        ],
        "spells": [
            {"name": "S1", "threshold": "2"},
            {"name": "S2", "cost": "bad", "effect": "e"},
            {"name": "S1"},
            "skip",
        ],
        "specialRules": [
            {"name": "R1", "description": "d", "hasRating": True},
            {"name": "R1"},
            {"no": "name"},
        ],
    }

    async def fake_get(url, *args, **kwargs):
        class R:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return payload

        return R()

    book = {
        "factionName": "F1",
        "versionString": "v9",
        "spells": [{"name": "BookSpell", "threshold": 1, "effect": "be"}],
        "specialRules": [{"name": "BookRule", "description": "br"}],
    }

    with patch("app.army_forge.import_service.fetch_first_army_book_json", new=AsyncMock(return_value=book)):
        with patch(
            "app.army_forge.import_service.httpx.AsyncClient.get",
            new=AsyncMock(side_effect=fake_get),
        ):
            with patch("app.army_forge.import_service.broadcast_to_game", new=AsyncMock()):
                r = await client.post(
                    f"/api/proxy/import-army/{code}",
                    json={
                        "army_forge_url": "https://army-forge.onepagerules.com/share?id=FAKE12345",
                        "player_id": hid,
                    },
                )
    assert r.status_code in (200, 201)


@pytest.mark.asyncio
async def test_import_faction_merge_and_caster_from_upgrades(client):
    resp = await client.post(
        "/api/games",
        json={"name": "ImpFac", "player_name": "H", "player_color": "#111"},
    )
    code = resp.json()["code"]
    hid = resp.json()["players"][0]["id"]

    units = [
        {
            "name": "C",
            "quality": 4,
            "defense": 4,
            "size": 1,
            "cost": 10,
            "rules": [],
            "loadout": [{"name": "Staff", "description": "Caster(2)"}],
            "selectedUpgrades": [{"name": "u", "description": "Caster(3)"}],
            "id": "u1",
            "selectionId": "s1",
        }
    ]

    async def fake_get(url, *args, **kwargs):
        class R:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {"units": units}

        return R()

    book = {"name": "F2", "factionName": None}

    with patch("app.army_forge.import_service.fetch_first_army_book_json", new=AsyncMock(return_value=book)):
        with patch("app.army_forge.import_service.httpx.AsyncClient.get", new=AsyncMock(side_effect=fake_get)):
            with patch("app.army_forge.import_service.broadcast_to_game", new=AsyncMock()):
                r = await client.post(
                    f"/api/proxy/import-army/{code}",
                    json={
                        "army_forge_url": "https://army-forge.onepagerules.com/share?id=FAKE12345",
                        "player_id": hid,
                    },
                )
    assert r.status_code in (200, 201)
    g = await client.get(f"/api/games/{code}")
    army = g.json()["players"][0].get("army_name") or ""
    assert "F2" in army or "Imported" in army
