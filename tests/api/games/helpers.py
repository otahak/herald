"""Shared async helpers for games API tests."""

from unittest.mock import AsyncMock, patch


async def create_game_with_manual_unit(client, *, is_caster: bool = False, caster_level: int = 0):
    """
    Create a game with host only, add one manual unit, return ``(code, host_id, unit_id)``.

    Mocks ``broadcast_to_game`` around the manual-unit POST to avoid WS side effects.
    """
    resp = await client.post(
        "/api/games",
        json={"name": "UnitTest", "player_name": "Host", "player_color": "#111111"},
    )
    code = resp.json()["code"]
    host_id = resp.json()["players"][0]["id"]
    payload = {
        "player_id": host_id,
        "name": "Test Squad",
        "quality": 3,
        "defense": 4,
        "size": 3,
        "tough": 1,
        "cost": 100,
        "is_caster": is_caster,
        "caster_level": caster_level,
    }
    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        resp_unit = await client.post(f"/api/games/{code}/units/manual", json=payload)
    assert resp_unit.status_code == 201
    unit_id = resp_unit.json()["id"]
    return code, host_id, unit_id
