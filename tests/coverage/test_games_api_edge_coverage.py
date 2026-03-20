"""HTTP coverage for lifecycle, meta, saves, units_combat, and units_state edge cases."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.api.games.helpers import create_game_with_manual_unit


@pytest.mark.asyncio
async def test_create_game_reload_failure_logs(client):
    with patch(
        "app.api.games.lifecycle.get_game_by_code",
        new=AsyncMock(side_effect=RuntimeError("reload failed")),
    ):
        r = await client.post(
            "/api/games",
            json={"name": "FailReload", "player_name": "H", "player_color": "#111"},
        )
    assert r.status_code == 500


@pytest.mark.asyncio
async def test_join_rejects_started_or_full(client):
    r = await client.post(
        "/api/games",
        json={"name": "JoinEdge", "player_name": "H", "player_color": "#111"},
    )
    code = r.json()["code"]
    await client.post(
        f"/api/games/{code}/join",
        json={"player_name": "G", "player_color": "#222"},
    )
    hid = r.json()["players"][0]["id"]
    gid = (await client.get(f"/api/games/{code}")).json()["players"][1]["id"]
    for pid in (hid, gid):
        await client.post(
            f"/api/games/{code}/units/manual",
            json={
                "player_id": pid,
                "name": "U",
                "quality": 4,
                "defense": 4,
                "size": 1,
                "tough": 1,
                "cost": 1,
            },
        )
    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        await client.post(f"/api/games/{code}/start")

    j2 = await client.post(
        f"/api/games/{code}/join",
        json={"player_name": "Third", "player_color": "#333"},
    )
    assert j2.status_code in (400, 422)

    r = await client.post(
        "/api/games",
        json={"name": "Full", "player_name": "A", "player_color": "#111"},
    )
    c2 = r.json()["code"]
    await client.post(
        f"/api/games/{c2}/join",
        json={"player_name": "B", "player_color": "#222"},
    )
    j3 = await client.post(
        f"/api/games/{c2}/join",
        json={"player_name": "C", "player_color": "#333"},
    )
    assert j3.status_code in (400, 422)


@pytest.mark.asyncio
async def test_start_game_validation_branches(client):
    r = await client.post(
        "/api/games",
        json={"name": "St", "player_name": "H", "player_color": "#111"},
    )
    code = r.json()["code"]
    s1 = await client.post(f"/api/games/{code}/start")
    assert s1.status_code in (400, 422)

    r2 = await client.post(
        "/api/games",
        json={"name": "St2", "player_name": "H", "player_color": "#111"},
    )
    code2 = r2.json()["code"]
    await client.post(
        f"/api/games/{code2}/join",
        json={"player_name": "G", "player_color": "#222"},
    )
    st = await client.post(f"/api/games/{code2}/start")
    assert st.status_code in (400, 422)

    r3 = await client.post(
        "/api/games",
        json={"name": "St3", "player_name": "H", "player_color": "#111"},
    )
    code3 = r3.json()["code"]
    j = await client.post(
        f"/api/games/{code3}/join",
        json={"player_name": "G", "player_color": "#222"},
    )
    hid = r3.json()["players"][0]["id"]
    gid = j.json()["players"][1]["id"]
    await client.post(
        f"/api/games/{code3}/units/manual",
        json={
            "player_id": hid,
            "name": "A",
            "quality": 4,
            "defense": 4,
            "size": 1,
            "tough": 1,
            "cost": 1,
        },
    )
    st2 = await client.post(f"/api/games/{code3}/start")
    assert st2.status_code in (400, 422)

    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        await client.post(
            f"/api/games/{code3}/units/manual",
            json={
                "player_id": gid,
                "name": "C",
                "quality": 4,
                "defense": 4,
                "size": 1,
                "tough": 1,
                "cost": 1,
            },
        )
        await client.post(f"/api/games/{code3}/start")
        st3 = await client.post(f"/api/games/{code3}/start")
    assert st3.status_code in (400, 422)

    rs = await client.post(
        "/api/games",
        json={
            "name": "SoloSt",
            "player_name": "H",
            "player_color": "#111",
            "is_solo": True,
        },
    )
    solo_code = rs.json()["code"]
    ss = await client.post(f"/api/games/{solo_code}/start")
    assert ss.status_code in (400, 422)


@pytest.mark.asyncio
async def test_patch_game_state_round_status_player(client):
    r = await client.post(
        "/api/games",
        json={"name": "Patch", "player_name": "H", "player_color": "#111"},
    )
    code = r.json()["code"]
    pid = r.json()["players"][0]["id"]
    await client.post(
        f"/api/games/{code}/join",
        json={"player_name": "G", "player_color": "#222"},
    )
    gjson = (await client.get(f"/api/games/{code}")).json()
    pids = [p["id"] for p in gjson["players"]]
    for pl in pids:
        await client.post(
            f"/api/games/{code}/units/manual",
            json={
                "player_id": pl,
                "name": "x",
                "quality": 4,
                "defense": 4,
                "size": 1,
                "tough": 1,
                "cost": 1,
            },
        )
    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        await client.post(f"/api/games/{code}/start")

    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        pr = await client.patch(
            f"/api/games/{code}/state",
            json={"current_round": 3, "status": "completed", "current_player_id": pid},
        )
    assert pr.status_code == 200


@pytest.mark.asyncio
async def test_meta_vp_player_not_found(client):
    r = await client.post(
        "/api/games",
        json={"name": "M1", "player_name": "H", "player_color": "#111"},
    )
    code = r.json()["code"]
    bad = uuid.uuid4()
    resp = await client.patch(
        f"/api/games/{code}/players/{bad}/victory-points",
        json={"delta": 1},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_meta_rename_solo_only_and_not_found(client):
    r = await client.post(
        "/api/games",
        json={"name": "M2", "player_name": "H", "player_color": "#111"},
    )
    code = r.json()["code"]
    hid = r.json()["players"][0]["id"]
    rnm = await client.patch(
        f"/api/games/{code}/players/{hid}",
        json={"name": "New"},
    )
    assert rnm.status_code in (400, 422)

    rs = await client.post(
        "/api/games",
        json={
            "name": "M3",
            "player_name": "H",
            "player_color": "#111",
            "is_solo": True,
        },
    )
    sc = rs.json()["code"]
    hid2 = rs.json()["players"][0]["id"]
    bad = uuid.uuid4()
    r2 = await client.patch(
        f"/api/games/{sc}/players/{bad}",
        json={"name": "X"},
    )
    assert r2.status_code == 404


@pytest.mark.asyncio
async def test_saves_reject_multiplayer_and_load_not_found(client):
    r = await client.post(
        "/api/games",
        json={"name": "Sav", "player_name": "H", "player_color": "#111"},
    )
    code = r.json()["code"]
    sv = await client.post(
        f"/api/games/{code}/save",
        json={"save_name": "n"},
    )
    assert sv.status_code in (400, 422)
    ls = await client.get(f"/api/games/{code}/saves")
    assert ls.status_code in (400, 422)

    rs = await client.post(
        "/api/games",
        json={
            "name": "SoloSav",
            "player_name": "H",
            "player_color": "#111",
            "is_solo": True,
        },
    )
    sc = rs.json()["code"]
    hid = rs.json()["players"][0]["id"]
    oid = rs.json()["players"][1]["id"]
    for pl in (hid, oid):
        await client.post(
            f"/api/games/{sc}/units/manual",
            json={
                "player_id": pl,
                "name": "u",
                "quality": 4,
                "defense": 4,
                "size": 1,
                "tough": 1,
                "cost": 1,
            },
        )
    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        await client.post(f"/api/games/{sc}/start")

    sa = await client.post(
        f"/api/games/{sc}/save",
        json={"save_name": "s1"},
    )
    assert sa.status_code == 201
    lst = await client.get(f"/api/games/{sc}/saves")
    assert lst.status_code == 200

    nf = await client.post(
        f"/api/games/{sc}/load",
        json={"save_id": str(uuid.uuid4())},
    )
    assert nf.status_code == 404


@pytest.mark.asyncio
async def test_units_combat_get_game_error_and_action_errors(client):
    code, hid, uid = await create_game_with_manual_unit(client)
    await client.post(
        f"/api/games/{code}/join",
        json={"player_name": "G", "player_color": "#222"},
    )
    await client.post(
        f"/api/games/{code}/units/manual",
        json={
            "player_id": (await client.get(f"/api/games/{code}")).json()["players"][1]["id"],
            "name": "g",
            "quality": 4,
            "defense": 4,
            "size": 1,
            "tough": 1,
            "cost": 1,
        },
    )
    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        await client.post(f"/api/games/{code}/start")

    with patch(
        "app.api.games.units_combat.get_game_by_code",
        new=AsyncMock(side_effect=ValueError("db")),
    ):
        e = await client.post(
            f"/api/games/{code}/units/{uid}/actions",
            json={"action": "hold"},
        )
    assert e.status_code == 500

    bad_unit = uuid.uuid4()
    nf = await client.post(
        f"/api/games/{code}/units/{bad_unit}/actions",
        json={"action": "hold"},
    )
    assert nf.status_code == 404

    with patch(
        "app.api.games.units_combat.log_event",
        new=AsyncMock(side_effect=RuntimeError("log boom")),
    ):
        boom = await client.post(
            f"/api/games/{code}/units/{uid}/actions",
            json={"action": "hold"},
        )
    assert boom.status_code == 500


@pytest.mark.asyncio
async def test_units_combat_shaken_invalid_target_cast(client):
    code, hid, uid = await create_game_with_manual_unit(client)
    j = await client.post(
        f"/api/games/{code}/join",
        json={"player_name": "G", "player_color": "#222"},
    )
    gid = j.json()["players"][1]["id"]
    await client.post(
        f"/api/games/{code}/units/manual",
        json={
            "player_id": gid,
            "name": "t",
            "quality": 4,
            "defense": 4,
            "size": 1,
            "tough": 1,
            "cost": 1,
        },
    )
    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        await client.post(f"/api/games/{code}/start")

    await client.patch(
        f"/api/games/{code}/units/{uid}",
        json={"is_shaken": True},
    )
    h = await client.post(
        f"/api/games/{code}/units/{uid}/actions",
        json={"action": "rush"},
    )
    assert h.status_code in (400, 422)

    g = (await client.get(f"/api/games/{code}")).json()
    tuid = [u["id"] for u in g["units"] if u["name"] == "t"][0]
    ch = await client.post(
        f"/api/games/{code}/units/{uid}/actions",
        json={"action": "charge", "target_unit_ids": ["not-a-uuid"]},
    )
    assert ch.status_code in (400, 422)

    dest = await client.patch(
        f"/api/games/{code}/units/{tuid}",
        json={"deployment_status": "destroyed"},
    )
    assert dest.status_code == 200
    ch2 = await client.post(
        f"/api/games/{code}/units/{uid}/actions",
        json={"action": "charge", "target_unit_ids": [tuid]},
    )
    assert ch2.status_code in (400, 422)

    miss = await client.post(
        f"/api/games/{code}/units/{uid}/actions",
        json={"action": "charge", "target_unit_ids": [str(uuid.uuid4())]},
    )
    assert miss.status_code in (400, 404)

    nc = await client.post(
        f"/api/games/{code}/units/{uuid.uuid4()}/cast",
        json={"spell_value": 1, "spell_name": "x", "success": True},
    )
    assert nc.status_code == 404

    with patch("app.api.games.units_combat.get_effective_caster", return_value=(False, 0)):
        nc2 = await client.post(
            f"/api/games/{code}/units/{uid}/cast",
            json={"spell_value": 1, "spell_name": "x", "success": True},
        )
    assert nc2.status_code in (400, 422)

    await client.patch(
        f"/api/games/{code}/units/{uid}",
        json={"is_shaken": False, "spell_tokens": 4},
    )
    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        with patch(
            "app.api.games.units_combat.get_effective_caster",
            return_value=(True, 2),
        ):
            await client.post(
                f"/api/games/{code}/units/{uid}/cast",
                json={"spell_value": 1, "spell_name": "x", "success": True},
            )
    sh = await client.patch(
        f"/api/games/{code}/units/{uid}",
        json={"is_shaken": True},
    )
    assert sh.status_code == 200
    csh = await client.post(
        f"/api/games/{code}/units/{uid}/cast",
        json={"spell_value": 1, "spell_name": "x", "success": True},
    )
    assert csh.status_code in (400, 422)

    await client.patch(
        f"/api/games/{code}/units/{uid}",
        json={"is_shaken": False, "spell_tokens": 4},
    )
    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        with patch(
            "app.api.games.units_combat.get_effective_caster",
            return_value=(True, 2),
        ):
            cs = await client.post(
                f"/api/games/{code}/units/{uid}/cast",
                json={
                    "spell_value": 1,
                    "spell_name": "Smite",
                    "success": True,
                    "target_unit_id": tuid,
                },
            )
    assert cs.status_code in (200, 201)


@pytest.mark.asyncio
async def test_units_combat_attached_heroes_iter_raises(client):
    code, hid, uid = await create_game_with_manual_unit(client)
    await client.post(
        f"/api/games/{code}/join",
        json={"player_name": "G", "player_color": "#222"},
    )
    gid = (await client.get(f"/api/games/{code}")).json()["players"][1]["id"]
    await client.post(
        f"/api/games/{code}/units/manual",
        json={
            "player_id": gid,
            "name": "tgt",
            "quality": 4,
            "defense": 4,
            "size": 1,
            "tough": 1,
            "cost": 1,
        },
    )
    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        await client.post(f"/api/games/{code}/start")

    class BadAttached:
        def __iter__(self):
            raise RuntimeError("no iter")

        def __bool__(self):
            return True

    with patch(
        "app.api.games.units_combat.get_game_by_code",
        new=AsyncMock(),
    ) as mock_gg:
        game = MagicMock()
        game.is_solo = False
        game.last_activity_at = None
        pl = MagicMock()
        u = MagicMock()
        u.id = uuid.UUID(uid) if isinstance(uid, str) else uid
        u.state = MagicMock(activated_this_round=False, is_shaken=False)
        u.display_name = "U"
        u.attached_heroes = BadAttached()
        pl.units = [u]
        pl.id = uuid.uuid4()
        game.players = [pl]
        mock_gg.return_value = game

        with patch("app.api.games.units_combat.log_event", new=AsyncMock()):
            with patch("app.api.games.units_combat.broadcast_if_not_solo", new=AsyncMock()):
                r = await client.post(
                    f"/api/games/{code}/units/{uid}/actions",
                    json={"action": "hold"},
                )
        assert r.status_code in (200, 201)


@pytest.mark.asyncio
async def test_units_state_errors(client):
    code, hid, uid = await create_game_with_manual_unit(client)
    missing = uuid.uuid4()
    r = await client.patch(
        f"/api/games/{code}/units/{missing}",
        json={"wounds_taken": 1},
    )
    assert r.status_code == 404

    r_clear_nf = await client.delete(
        f"/api/games/{code}/players/{uuid.uuid4()}/units",
    )
    assert r_clear_nf.status_code == 404

    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        j = await client.post(
            f"/api/games/{code}/join",
            json={"player_name": "G", "player_color": "#222"},
        )
        gid = j.json()["players"][1]["id"]
        await client.post(
            f"/api/games/{code}/units/manual",
            json={
                "player_id": gid,
                "name": "Gu",
                "quality": 4,
                "defense": 4,
                "size": 1,
                "tough": 1,
                "cost": 1,
            },
        )
        st = await client.post(f"/api/games/{code}/start")
    assert st.status_code == 201

    man = await client.post(
        f"/api/games/{code}/units/manual",
        json={
            "player_id": hid,
            "name": "Late",
            "quality": 4,
            "defense": 4,
            "size": 1,
            "tough": 1,
            "cost": 1,
        },
    )
    assert man.status_code in (400, 422)

    dt = await client.patch(
        f"/api/games/{code}/units/{uid}/detach",
    )
    assert dt.status_code in (400, 422)

    bad_detach = await client.patch(
        f"/api/games/{code}/units/{uuid.uuid4()}/detach",
    )
    assert bad_detach.status_code == 404

    pr = await client.patch(
        f"/api/games/{code}/units/{uuid.uuid4()}/profile",
        json={"custom_name": "x"},
    )
    assert pr.status_code in (400, 404)


@pytest.mark.asyncio
async def test_manual_unit_attachment_errors(client):
    r = await client.post(
        "/api/games",
        json={"name": "Att", "player_name": "H", "player_color": "#111"},
    )
    code = r.json()["code"]
    hid = r.json()["players"][0]["id"]
    p1 = await client.post(
        f"/api/games/{code}/units/manual",
        json={
            "player_id": hid,
            "name": "Parent",
            "quality": 4,
            "defense": 4,
            "size": 1,
            "tough": 1,
            "cost": 1,
        },
    )
    pid = p1.json()["id"]
    e1 = await client.post(
        f"/api/games/{code}/units/manual",
        json={
            "player_id": hid,
            "name": "Hero",
            "quality": 4,
            "defense": 4,
            "size": 1,
            "tough": 1,
            "cost": 1,
            "attached_to_unit_id": str(uuid.uuid4()),
        },
    )
    assert e1.status_code == 404

    j = await client.post(
        f"/api/games/{code}/join",
        json={"player_name": "G", "player_color": "#222"},
    )
    gid = j.json()["players"][1]["id"]
    e2 = await client.post(
        f"/api/games/{code}/units/manual",
        json={
            "player_id": gid,
            "name": "Hero2",
            "quality": 4,
            "defense": 4,
            "size": 1,
            "tough": 1,
            "cost": 1,
            "attached_to_unit_id": pid,
        },
    )
    assert e2.status_code in (400, 422)

    e3 = await client.post(
        f"/api/games/{code}/units/manual",
        json={
            "player_id": str(uuid.uuid4()),
            "name": "BadP",
            "quality": 4,
            "defense": 4,
            "size": 1,
            "tough": 1,
            "cost": 1,
        },
    )
    assert e3.status_code in (400, 404, 422)


@pytest.mark.asyncio
async def test_delete_unit_broadcasts_when_multiplayer(client):
    r = await client.post(
        "/api/games",
        json={"name": "DelU", "player_name": "H", "player_color": "#111"},
    )
    code = r.json()["code"]
    hid = r.json()["players"][0]["id"]
    await client.post(
        f"/api/games/{code}/join",
        json={"player_name": "G", "player_color": "#222"},
    )
    u = await client.post(
        f"/api/games/{code}/units/manual",
        json={
            "player_id": hid,
            "name": "DelMe",
            "quality": 4,
            "defense": 4,
            "size": 1,
            "tough": 1,
            "cost": 1,
        },
    )
    uid = u.json()["id"]
    with patch("app.api.games.units_state.broadcast_to_game", new=AsyncMock()) as bc:
        d = await client.delete(f"/api/games/{code}/units/{uid}")
    assert d.status_code == 200
    bc.assert_awaited()
