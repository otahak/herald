"""Coverage for ``websocket.py`` ``GameRoom``, manager, ``get_game_state``, and handler paths."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.websocket import (
    GameRoom,
    GameRoomManager,
    broadcast_to_game,
    get_game_state,
    room_manager,
)


@pytest.mark.asyncio
async def test_game_room_broadcast_send_failure_cleans_connections():
    room = GameRoom("AB")
    p1, p2 = uuid.uuid4(), uuid.uuid4()
    w1, w2 = AsyncMock(), AsyncMock()
    w1.send_json = AsyncMock(side_effect=RuntimeError("x"))
    w2.send_json = AsyncMock()
    room.connections[p1] = w1
    room.connections[p2] = w2
    aw = AsyncMock()
    aw.send_json = AsyncMock(side_effect=RuntimeError("y"))
    room.anonymous_connections.add(aw)
    await room.broadcast({"type": "x"}, exclude=None)
    assert p1 not in room.connections
    assert p2 in room.connections
    assert aw not in room.anonymous_connections


@pytest.mark.asyncio
async def test_game_room_broadcast_excludes_player():
    room = GameRoom("EX")
    a, b = uuid.uuid4(), uuid.uuid4()
    wa, wb = AsyncMock(), AsyncMock()
    wa.send_json = AsyncMock()
    wb.send_json = AsyncMock()
    room.connections[a] = wa
    room.connections[b] = wb
    await room.broadcast({"t": 1}, exclude=a)
    wa.send_json.assert_not_awaited()
    wb.send_json.assert_awaited()


@pytest.mark.asyncio
async def test_game_room_send_to_success_and_failure():
    room = GameRoom("CD")
    pid = uuid.uuid4()
    w = AsyncMock()
    w.send_json = AsyncMock(side_effect=OSError("z"))
    room.connections[pid] = w
    assert await room.send_to(pid, {"a": 1}) is False
    assert pid not in room.connections
    w2 = AsyncMock()
    w2.send_json = AsyncMock()
    room.connections[pid] = w2
    assert await room.send_to(pid, {"b": 2}) is True


def test_game_room_add_remove_and_counts():
    room = GameRoom("EF")
    w = MagicMock()
    pid = uuid.uuid4()
    room.add_anonymous_connection(w)
    room.add_connection(pid, w)
    assert room.connection_count == 1
    room.remove_connection(pid, w)
    assert room.connection_count == 0
    room.remove_connection(None, w)


def test_room_manager_get_remove_all():
    m = GameRoomManager()
    r1 = m.get_room("aa")
    r2 = m.get_room("AA")
    assert r1 is r2
    assert "AA" in m.get_all_rooms()
    m.remove_room("aa")
    assert "AA" not in m._rooms


@pytest.mark.asyncio
async def test_broadcast_to_game_skips_empty_room():
    room_manager.remove_room("ZZEMPTY")
    room_manager.get_room("ZZEMPTY")
    await broadcast_to_game("ZZEMPTY", {"type": "noop"})


@pytest.mark.asyncio
async def test_broadcast_to_game_with_connections():
    room_manager.remove_room("ZZFULL")
    r = room_manager.get_room("ZZFULL")
    w = AsyncMock()
    w.send_json = AsyncMock()
    r.add_anonymous_connection(w)
    await broadcast_to_game("ZZFULL", {"type": "ping"})


@pytest.mark.asyncio
async def test_get_game_state_returns_none_when_missing():
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=result)
    assert await get_game_state(session, "NOPE999") is None


@pytest.mark.asyncio
async def test_get_game_state_serializes_units_states_and_objectives():
    from app.models.game import GameStatus, GameSystem

    st = MagicMock()
    st.id = uuid.uuid4()
    st.wounds_taken = 0
    st.models_remaining = 1
    st.activated_this_round = False
    st.is_shaken = False
    st.is_fatigued = False
    st.deployment_status = MagicMock()
    st.deployment_status.value = "deployed"
    st.transport_id = None
    st.spell_tokens = 0
    st.limited_weapons_used = None
    st.custom_notes = None

    u1 = MagicMock()
    u1.id = uuid.uuid4()
    u1.player_id = uuid.uuid4()
    u1.name = "A"
    u1.custom_name = None
    u1.quality = 4
    u1.defense = 4
    u1.size = 1
    u1.tough = 1
    u1.cost = 10
    u1.loadout = []
    u1.rules = []
    u1.is_hero = False
    u1.is_caster = False
    u1.caster_level = 0
    u1.is_transport = False
    u1.transport_capacity = 0
    u1.has_ambush = False
    u1.has_scout = False
    u1.attached_to_unit_id = None
    u1.upgrades = None
    u1.state = st

    u2 = MagicMock()
    u2.id = uuid.uuid4()
    u2.player_id = u1.player_id
    u2.name = "B"
    u2.custom_name = None
    u2.quality = 3
    u2.defense = 3
    u2.size = 1
    u2.tough = 1
    u2.cost = 5
    u2.loadout = []
    u2.rules = []
    u2.is_hero = False
    u2.is_caster = False
    u2.caster_level = 0
    u2.is_transport = False
    u2.transport_capacity = 0
    u2.has_ambush = False
    u2.has_scout = False
    u2.attached_to_unit_id = None
    u2.upgrades = None
    u2.state = None

    pl = MagicMock()
    pl.id = uuid.uuid4()
    pl.name = "P"
    pl.color = "#111"
    pl.is_host = True
    pl.is_connected = True
    pl.army_name = None
    pl.army_forge_list_id = None
    pl.starting_unit_count = 0
    pl.starting_points = 0
    pl.has_finished_activations = False
    pl.spells = None
    pl.special_rules = None
    pl.faction_name = None
    pl.army_book_version = None
    pl.victory_points = 0
    pl.units = [u1, u2]

    obj = MagicMock()
    obj.id = uuid.uuid4()
    obj.marker_number = 1
    obj.label = None
    obj.status = MagicMock()
    obj.status.value = "neutral"
    obj.controlled_by_id = None

    game = MagicMock()
    game.id = uuid.uuid4()
    game.code = "ABCDE"
    game.name = "G"
    game.game_system = GameSystem.GFF
    game.status = GameStatus.IN_PROGRESS
    game.current_round = 1
    game.max_rounds = 5
    game.current_player_id = pl.id
    game.first_player_next_round_id = None
    game.players = [pl]
    game.objectives = [obj]

    session = AsyncMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = game
    session.execute = AsyncMock(return_value=res)

    data = await get_game_state(session, "abcde")
    assert data["code"] == "ABCDE"
    assert len(data["units"]) == 2
    assert data["units"][0]["state"] is not None
    assert data["units"][1]["state"] is None
    assert len(data["objectives"]) == 1




@pytest.mark.asyncio
async def test_websocket_solo_rejected(sync_client):
    from litestar.exceptions.websocket_exceptions import WebSocketDisconnect

    r = sync_client.post(
        "/api/games",
        json={
            "name": "SoloWS",
            "player_name": "H",
            "player_color": "#111",
            "is_solo": True,
        },
    )
    code = r.json()["code"]
    with pytest.raises(WebSocketDisconnect, match="Solo"):
        with sync_client.websocket_connect(f"/ws/game/{code}"):
            pass


@pytest.mark.asyncio
async def test_websocket_game_not_found(sync_client):
    from litestar.exceptions.websocket_exceptions import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with sync_client.websocket_connect("/ws/game/ZZZZZZ"):
            pass


@pytest.mark.asyncio
async def test_websocket_error_when_state_missing(sync_client, monkeypatch):
    async def fake_get_game(session, c):
        g = MagicMock()
        g.is_solo = False
        return g

    async def fake_state(session, c):
        return None

    monkeypatch.setattr("app.api.game_helpers.get_game_by_code", fake_get_game)
    monkeypatch.setattr("app.api.websocket.get_game_state", fake_state)
    with sync_client.websocket_connect("/ws/game/ANY") as socket:
        msg = socket.receive_json()
        assert msg["type"] == "error"


@pytest.mark.asyncio
async def test_websocket_join_ping_state_update_unknown(sync_client):
    r = sync_client.post(
        "/api/games",
        json={"name": "MPWS", "player_name": "H", "player_color": "#111"},
    )
    code = r.json()["code"]
    sync_client.post(
        f"/api/games/{code}/join",
        json={"player_name": "G", "player_color": "#222"},
    )
    gr = sync_client.get(f"/api/games/{code}")
    guest_id = next(p["id"] for p in gr.json()["players"] if p["name"] == "G")
    with sync_client.websocket_connect(f"/ws/game/{code}") as socket:
        socket.receive_json()
        socket.send_json({"type": "join", "player_id": guest_id})
        socket.send_json({"type": "ping"})
        assert socket.receive_json()["type"] == "pong"
        socket.send_json({"type": "request_state"})
        st = socket.receive_json()
        assert st["type"] == "state"
        socket.send_json({"type": "state_update", "data": {"reason": "x"}})
        socket.send_json({"type": "not_a_real_type"})
        err = socket.receive_json()
        assert err["type"] == "error"


@pytest.mark.asyncio
async def test_websocket_join_invalid_player_id(sync_client):
    r = sync_client.post(
        "/api/games",
        json={"name": "J2", "player_name": "H", "player_color": "#111"},
    )
    code = r.json()["code"]
    with sync_client.websocket_connect(f"/ws/game/{code}") as socket:
        socket.receive_json()
        socket.send_json({"type": "join", "player_id": "not-uuid"})
        msg = socket.receive_json()
        assert msg["type"] == "error"


@pytest.mark.asyncio
async def test_websocket_join_unknown_player(sync_client):
    r = sync_client.post(
        "/api/games",
        json={"name": "J3", "player_name": "H", "player_color": "#111"},
    )
    code = r.json()["code"]
    with sync_client.websocket_connect(f"/ws/game/{code}") as socket:
        socket.receive_json()
        socket.send_json({"type": "join", "player_id": str(uuid.uuid4())})
        msg = socket.receive_json()
        assert msg["type"] == "error"

