"""Cover remaining single-digit statement gaps across app/."""

import json as _json_mod
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.game import GameStatus
from app.models.unit import DeploymentStatus
from app.utils.unit_stats import parse_stat_modifications
from tests.api.games.helpers import create_game_with_manual_unit

_REAL_JSON_LOADS = _json_mod.loads


@pytest.mark.asyncio
async def test_start_solo_rejects_zero_players(client):
    import app.api.games.lifecycle as lc

    g = MagicMock()
    g.is_solo = True
    g.players = []
    g.status = GameStatus.LOBBY
    g.last_activity_at = None

    with patch.object(lc, "get_game_by_code", new=AsyncMock(return_value=g)):
        r = await client.post("/api/games/SOLOZZ/start")
    assert r.status_code in (400, 404, 422)


@pytest.mark.asyncio
async def test_solo_save_load_roundtrip(client):
    from unittest.mock import AsyncMock as AM

    r = await client.post(
        "/api/games",
        json={
            "name": "SoloRL",
            "player_name": "H",
            "player_color": "#111",
            "is_solo": True,
        },
    )
    sc = r.json()["code"]
    pids = [p["id"] for p in r.json()["players"]]
    for pid in pids:
        await client.post(
            f"/api/games/{sc}/units/manual",
            json={
                "player_id": pid,
                "name": "u",
                "quality": 4,
                "defense": 4,
                "size": 1,
                "tough": 1,
                "cost": 1,
            },
        )
    with patch("app.api.game_helpers.broadcast_to_game", new=AM()):
        await client.post(f"/api/games/{sc}/start")

    sv = await client.post(
        f"/api/games/{sc}/save",
        json={"save_name": "full"},
    )
    assert sv.status_code == 201
    sid = sv.json()["save_id"]

    ld = await client.post(
        f"/api/games/{sc}/load",
        json={"save_id": sid},
    )
    assert ld.status_code == 200


@pytest.mark.asyncio
async def test_update_unit_state_no_unit_state_validation(client):
    import app.api.games.units_state as us

    uid = uuid.uuid4()
    u = MagicMock()
    u.id = uid
    u.state = None
    pl = MagicMock()
    pl.units = [u]
    game = MagicMock()
    game.players = [pl]
    game.last_activity_at = None

    with patch.object(us, "get_game_by_code", new=AsyncMock(return_value=game)):
        r = await client.patch(
            "/api/games/FAKECD/units/%s" % uid,
            json={"wounds_taken": 1},
        )
    assert r.status_code in (400, 422)


@pytest.mark.asyncio
async def test_profile_update_unit_not_found_in_lobby(client):
    r = await client.post(
        "/api/games",
        json={"name": "Prof", "player_name": "H", "player_color": "#111"},
    )
    code = r.json()["code"]
    pr = await client.patch(
        f"/api/games/{code}/units/{uuid.uuid4()}/profile",
        json={"custom_name": "z"},
    )
    assert pr.status_code == 404


@pytest.mark.asyncio
async def test_log_unit_action_no_state_validation(client):
    import app.api.games.units_combat as uc

    uid = uuid.uuid4()
    u = MagicMock()
    u.id = uid
    u.state = None
    u.display_name = "X"
    pl = MagicMock()
    pl.id = uuid.uuid4()
    pl.units = [u]
    game = MagicMock()
    game.players = [pl]
    game.last_activity_at = None

    with patch.object(uc, "get_game_by_code", new=AsyncMock(return_value=game)):
        r = await client.post(
            f"/api/games/FAKECD/units/{uid}/actions",
            json={"action": "hold"},
        )
    assert r.status_code in (400, 422)


@pytest.mark.asyncio
async def test_cast_no_state_and_shaken(client):
    import app.api.games.units_combat as uc

    uid = uuid.uuid4()
    u = MagicMock()
    u.id = uid
    u.state = None
    pl = MagicMock()
    pl.units = [u]
    game = MagicMock()
    game.players = [pl]
    game.last_activity_at = None

    with patch.object(uc, "get_game_by_code", new=AsyncMock(return_value=game)):
        r = await client.post(
            f"/api/games/FAKECD/units/{uid}/cast",
            json={"spell_value": 1, "spell_name": "s", "success": True},
        )
    assert r.status_code in (400, 422)

    st = MagicMock()
    st.is_shaken = True
    st.spell_tokens = 3
    u2 = MagicMock()
    u2.id = uid
    u2.state = st
    pl2 = MagicMock()
    pl2.units = [u2]
    game2 = MagicMock()
    game2.players = [pl2]
    game2.last_activity_at = None

    with patch.object(uc, "get_game_by_code", new=AsyncMock(return_value=game2)):
        with patch.object(uc, "get_effective_caster", return_value=(True, 2)):
            r2 = await client.post(
                f"/api/games/FAKECD/units/{uid}/cast",
                json={"spell_value": 1, "spell_name": "s", "success": True},
            )
    assert r2.status_code in (400, 422)


@pytest.mark.asyncio
async def test_log_action_primary_target_uuid_string(client):
    import app.api.games.units_combat as uc

    uid = uuid.uuid4()
    tid = uuid.uuid4()
    st = MagicMock(activated_this_round=False, is_shaken=False)
    u = MagicMock()
    u.id = uid
    u.state = st
    u.display_name = "A"
    u.attached_heroes = []
    tgt = MagicMock()
    tgt.id = tid
    tgt.state = MagicMock()
    tgt.state.deployment_status = DeploymentStatus.DEPLOYED
    tgt.display_name = "T"

    hp = MagicMock()
    hp.id = uuid.uuid4()
    hp.units = [u]
    gp = MagicMock()
    gp.id = uuid.uuid4()
    gp.units = [tgt]
    game = MagicMock()
    game.players = [hp, gp]
    game.last_activity_at = None

    with patch.object(uc, "get_game_by_code", new=AsyncMock(return_value=game)):
        with patch.object(uc, "log_event", new=AsyncMock()):
            with patch.object(uc, "broadcast_if_not_solo", new=AsyncMock()):
                r = await client.post(
                    f"/api/games/FAKECD/units/{uid}/actions",
                    json={"action": "charge", "target_unit_ids": [str(tid)]},
                )
    assert r.status_code in (200, 201)


@pytest.mark.asyncio
async def test_log_action_attached_hero_gets_activated(client):
    import app.api.games.units_combat as uc

    uid = uuid.uuid4()
    hid = uuid.uuid4()
    st = MagicMock(activated_this_round=False, is_shaken=False)
    hs = MagicMock(activated_this_round=False)
    hero = MagicMock()
    hero.state = hs
    u = MagicMock()
    u.id = uid
    u.state = st
    u.display_name = "P"
    u.attached_heroes = [hero]
    pl = MagicMock()
    pl.id = uuid.uuid4()
    pl.units = [u]
    game = MagicMock()
    game.players = [pl]
    game.is_solo = True
    game.last_activity_at = None

    with patch.object(uc, "get_game_by_code", new=AsyncMock(return_value=game)):
        with patch.object(uc, "log_event", new=AsyncMock()):
            with patch.object(uc, "broadcast_if_not_solo", new=AsyncMock()):
                r = await client.post(
                    f"/api/games/FAKECD/units/{uid}/actions",
                    json={"action": "hold"},
                )
    assert r.status_code in (200, 201)
    assert hs.activated_this_round is True


def test_parse_additive_defense_negative_via_suffix_hyphen():
    mods = parse_stat_modifications(rules=[{"description": "-1-1 d"}])
    assert mods["defense"] == -1


def test_parse_additive_quality_negative_via_suffix_hyphen():
    mods = parse_stat_modifications(rules=[{"description": "-1-1 q"}])
    assert mods["quality"] == -1


def test_parse_upgrade_empty_skipped():
    assert parse_stat_modifications(upgrades=[None])["quality"] == 0


def test_parse_loadout_none_skipped():
    assert parse_stat_modifications(loadout=[None])["defense"] == 0


def test_parse_upgrade_nested_absolute_merge():
    mods = parse_stat_modifications(
        rules=[{"description": "armor(5+)"}],
        upgrades=[
            {
                "rules": [{"description": "armor(6+)"}],
            }
        ],
    )
    assert mods["defense"] == 6


@pytest.mark.asyncio
async def test_load_save_rejects_multiplayer(client):
    r = await client.post(
        "/api/games",
        json={"name": "LdMp", "player_name": "H", "player_color": "#111"},
    )
    code = r.json()["code"]
    await client.post(
        f"/api/games/{code}/join",
        json={"player_name": "G", "player_color": "#222"},
    )
    ld = await client.post(
        f"/api/games/{code}/load",
        json={"save_id": str(uuid.uuid4())},
    )
    assert ld.status_code in (400, 422)


@pytest.mark.asyncio
async def test_solo_save_load_objectives_transport_attach(client):
    import app.api.games.saves as saves_mod

    r = await client.post(
        "/api/games",
        json={
            "name": "SoloFull",
            "player_name": "H",
            "player_color": "#111",
            "is_solo": True,
        },
    )
    sc = r.json()["code"]
    p0, p1 = r.json()["players"][0]["id"], r.json()["players"][1]["id"]

    await client.post(f"/api/games/{sc}/objectives", json={"count": 3})

    tr = await client.post(
        f"/api/games/{sc}/units/manual",
        json={
            "player_id": p0,
            "name": "Tr",
            "quality": 4,
            "defense": 4,
            "size": 1,
            "tough": 1,
            "cost": 50,
            "is_transport": True,
            "transport_capacity": 2,
        },
    )
    tid = tr.json()["id"]
    pr = await client.post(
        f"/api/games/{sc}/units/manual",
        json={
            "player_id": p0,
            "name": "Pass",
            "quality": 4,
            "defense": 4,
            "size": 1,
            "tough": 1,
            "cost": 10,
        },
    )
    pid = pr.json()["id"]
    hr = await client.post(
        f"/api/games/{sc}/units/manual",
        json={
            "player_id": p0,
            "name": "Hero",
            "quality": 4,
            "defense": 4,
            "size": 1,
            "tough": 1,
            "cost": 20,
            "attached_to_unit_id": pid,
        },
    )
    assert hr.status_code == 201

    await client.patch(
        f"/api/games/{sc}/units/{pid}",
        json={"transport_id": tid},
    )

    await client.post(
        f"/api/games/{sc}/units/manual",
        json={
            "player_id": p1,
            "name": "Opp",
            "quality": 4,
            "defense": 4,
            "size": 1,
            "tough": 1,
            "cost": 10,
        },
    )

    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        await client.post(f"/api/games/{sc}/start")

    sv = await client.post(
        f"/api/games/{sc}/save",
        json={"save_name": "complex"},
    )
    assert sv.status_code == 201
    sid = sv.json()["save_id"]

    def loads_wrapper(s):
        data = _REAL_JSON_LOADS(s)
        data["current_player_id"] = uuid.UUID(data["current_player_id"])
        return data

    with patch.object(saves_mod.json, "loads", new=loads_wrapper):
        ld = await client.post(
            f"/api/games/{sc}/load",
            json={"save_id": sid},
        )
    assert ld.status_code == 200


@pytest.mark.asyncio
async def test_charge_invalid_uuid_target(client):
    code, hid, uid = await create_game_with_manual_unit(client)
    await client.post(
        f"/api/games/{code}/join",
        json={"player_name": "G", "player_color": "#222"},
    )
    await client.post(
        f"/api/games/{code}/units/manual",
        json={
            "player_id": (await client.get(f"/api/games/{code}")).json()["players"][1]["id"],
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

    ch = await client.post(
        f"/api/games/{code}/units/{uid}/actions",
        json={"action": "charge", "target_unit_ids": ["!!!bad!!!"]},
    )
    assert ch.status_code in (400, 422)


@pytest.mark.asyncio
async def test_charge_destroyed_and_missing_target(client):
    code, hid, uid = await create_game_with_manual_unit(client)
    j = await client.post(
        f"/api/games/{code}/join",
        json={"player_name": "G", "player_color": "#222"},
    )
    gid = j.json()["players"][1]["id"]
    tresp = await client.post(
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
    tuid = tresp.json()["id"]
    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()):
        await client.post(f"/api/games/{code}/start")

    await client.patch(
        f"/api/games/{code}/units/{tuid}",
        json={"deployment_status": "destroyed"},
    )
    ch = await client.post(
        f"/api/games/{code}/units/{uid}/actions",
        json={"action": "charge", "target_unit_ids": [tuid]},
    )
    assert ch.status_code in (400, 422)

    miss = await client.post(
        f"/api/games/{code}/units/{uid}/actions",
        json={"action": "attack", "target_unit_ids": [str(uuid.uuid4())]},
    )
    assert miss.status_code == 404


@pytest.mark.asyncio
async def test_import_caster_flags_and_faction_merge_and_spell_paths(client):
    r = await client.post(
        "/api/games",
        json={"name": "ImpFin", "player_name": "H", "player_color": "#111"},
    )
    code = r.json()["code"]
    hid = r.json()["players"][0]["id"]

    units = [
        {
            "name": "Casty",
            "quality": 4,
            "defense": 4,
            "size": 1,
            "cost": 10,
            "rules": [],
            "loadout": [{"name": "Caster", "rating": 2}],
            "selectedUpgrades": [{"name": "Caster", "rating": 3}],
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
                return {
                    "units": units,
                    "spells": [
                        None,
                        {"name": "ArmyBadTh", "threshold": "no"},
                        {"name": "ArmyBadC", "cost": "no"},
                        {"name": "Dup", "threshold": 2},
                    ],
                }

        return R()

    book1 = {
        "factionName": "Alpha",
        "spells": [
            None,
            {"name": "BadTh", "threshold": "no"},
            {"name": "BadC", "cost": "no"},
            {"name": "Dup", "threshold": 3},
            {"name": "NewS", "cost": "5", "text": "t"},
            {"name": "Th", "threshold": "4"},
        ],
        "specialRules": [
            {"name": "SR", "description": "d"},
            {"name": "SR"},
            {"description": "noname"},
        ],
    }

    with patch(
        "app.army_forge.import_service.fetch_first_army_book_json",
        new=AsyncMock(return_value=book1),
    ):
        with patch(
            "app.army_forge.import_service.httpx.AsyncClient.get",
            new=AsyncMock(side_effect=fake_get),
        ):
            with patch("app.army_forge.import_service.broadcast_to_game", new=AsyncMock()):
                i1 = await client.post(
                    f"/api/proxy/import-army/{code}",
                    json={
                        "army_forge_url": "https://army-forge.onepagerules.com/share?id=FAKE12345",
                        "player_id": hid,
                    },
                )
    assert i1.status_code in (200, 201)

    book2 = {
        "factionName": "Beta",
        "specialRules": [{"name": "SR", "description": "again"}],
    }
    with patch(
        "app.army_forge.import_service.fetch_first_army_book_json",
        new=AsyncMock(return_value=book2),
    ):
        with patch(
            "app.army_forge.import_service.httpx.AsyncClient.get",
            new=AsyncMock(side_effect=fake_get),
        ):
            with patch("app.army_forge.import_service.broadcast_to_game", new=AsyncMock()):
                i2 = await client.post(
                    f"/api/proxy/import-army/{code}",
                    json={
                        "army_forge_url": "https://army-forge.onepagerules.com/share?id=FAKE99999",
                        "player_id": hid,
                    },
                )
    assert i2.status_code in (200, 201)
    g = await client.get(f"/api/games/{code}")
    fn = g.json()["players"][0].get("faction_name") or ""
    assert "Alpha" in fn and "Beta" in fn
