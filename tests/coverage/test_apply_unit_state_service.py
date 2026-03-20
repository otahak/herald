"""Targeted coverage for app.services.games.unit_state.apply_update_unit_state."""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.game_schemas import UpdateUnitStateRequest
from app.models import DeploymentStatus, EventType
from app.services.games.errors import UnitStateValidationError
from app.services.games import unit_state as us_mod


def _state(**kw):
    defaults = dict(
        wounds_taken=0,
        models_remaining=1,
        activated_this_round=False,
        is_shaken=False,
        is_fatigued=False,
        deployment_status=DeploymentStatus.DEPLOYED,
        transport_id=None,
        spell_tokens=0,
        limited_weapons_used=None,
        custom_notes=None,
    )
    defaults.update(kw)
    m = MagicMock()
    for k, v in defaults.items():
        setattr(m, k, v)
    return m


def _unit(uid, pid, **kw):
    u = MagicMock()
    u.id = uid
    u.player_id = pid
    u.display_name = kw.get("display_name", "U")
    u.max_wounds = kw.get("max_wounds", 3)
    u.attached_to_unit_id = kw.get("attached_to_unit_id")
    u.attached_heroes = kw.get("attached_heroes", [])
    u.is_transport = kw.get("is_transport", False)
    st = kw.get("state")
    u.state = st
    return u


def _game_with_unit(unit):
    g = MagicMock()
    g.id = uuid.uuid4()
    g.current_round = 1
    pl = MagicMock()
    pl.units = [unit]
    g.players = [pl]
    return g


@pytest.mark.asyncio
async def test_wound_increase_logs_each_wound():
    uid = uuid.uuid4()
    pid = uuid.uuid4()
    st = _state(wounds_taken=0)
    u = _unit(uid, pid, state=st, max_wounds=4)
    g = _game_with_unit(u)
    session = AsyncMock()

    with patch.object(us_mod, "log_event", new=AsyncMock()) as le:
        await us_mod.apply_update_unit_state(
            session, g, u, uid, UpdateUnitStateRequest(wounds_taken=2)
        )
    assert st.wounds_taken == 2
    assert le.await_count == 2


@pytest.mark.asyncio
async def test_wound_decrease_deletes_recent_events():
    uid = uuid.uuid4()
    pid = uuid.uuid4()
    st = _state(wounds_taken=2)
    u = _unit(uid, pid, state=st)
    g = _game_with_unit(u)
    session = AsyncMock()
    fixed = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    ev = MagicMock()
    ev.created_at = fixed - timedelta(seconds=5)

    exec_result = MagicMock()
    exec_result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[ev])))

    async def exec_side_effect(*a, **kw):
        return exec_result

    session.execute = AsyncMock(side_effect=exec_side_effect)

    with patch.object(us_mod, "log_event", new=AsyncMock()):
        with patch.object(us_mod, "datetime") as dm:
            dm.now = MagicMock(return_value=fixed)
            dm.timedelta = timedelta
            dm.timezone = timezone
            await us_mod.apply_update_unit_state(
                session, g, u, uid, UpdateUnitStateRequest(wounds_taken=1)
            )
    session.delete.assert_awaited_once_with(ev)


@pytest.mark.asyncio
async def test_wound_decrease_old_events_log_heal():
    uid = uuid.uuid4()
    pid = uuid.uuid4()
    st = _state(wounds_taken=2)
    u = _unit(uid, pid, state=st)
    g = _game_with_unit(u)
    session = AsyncMock()
    fixed = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    ev = MagicMock()
    ev.created_at = fixed - timedelta(minutes=5)

    exec_result = MagicMock()
    exec_result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[ev])))

    session.execute = AsyncMock(return_value=exec_result)

    with patch.object(us_mod, "log_event", new=AsyncMock()) as le:
        with patch.object(us_mod, "datetime") as dm:
            dm.now = MagicMock(return_value=fixed)
            dm.timedelta = timedelta
            dm.timezone = timezone
            await us_mod.apply_update_unit_state(
                session, g, u, uid, UpdateUnitStateRequest(wounds_taken=1)
            )
    assert le.await_args_list[0].args[2] == EventType.UNIT_HEALED


@pytest.mark.asyncio
async def test_activate_attached_raises_when_attached_to_parent():
    uid = uuid.uuid4()
    pid = uuid.uuid4()
    st = _state()
    u = _unit(uid, pid, state=st, attached_to_unit_id=uuid.uuid4())
    g = _game_with_unit(u)
    session = AsyncMock()
    with pytest.raises(UnitStateValidationError):
        await us_mod.apply_update_unit_state(
            session, g, u, uid, UpdateUnitStateRequest(activated_this_round=True)
        )


@pytest.mark.asyncio
async def test_activate_parent_logs_attached_heroes():
    uid = uuid.uuid4()
    pid = uuid.uuid4()
    h = _unit(uuid.uuid4(), pid)
    h.state = _state(activated_this_round=False)
    st = _state()
    u = _unit(uid, pid, state=st, attached_heroes=[h])
    g = _game_with_unit(u)
    session = AsyncMock()
    with patch.object(us_mod, "log_event", new=AsyncMock()) as le:
        await us_mod.apply_update_unit_state(
            session, g, u, uid, UpdateUnitStateRequest(activated_this_round=True)
        )
    assert h.state.activated_this_round is True
    assert le.await_count >= 2


@pytest.mark.asyncio
async def test_shaken_attached_heroes_mirror():
    uid = uuid.uuid4()
    pid = uuid.uuid4()
    h = _unit(uuid.uuid4(), pid)
    h.state = _state(is_shaken=False)
    st = _state(is_shaken=False)
    u = _unit(uid, pid, state=st, attached_heroes=[h])
    g = _game_with_unit(u)
    session = AsyncMock()
    with patch.object(us_mod, "log_event", new=AsyncMock()):
        await us_mod.apply_update_unit_state(
            session, g, u, uid, UpdateUnitStateRequest(is_shaken=True)
        )
    assert h.state.is_shaken is True


@pytest.mark.asyncio
async def test_shaken_clear_attached():
    uid = uuid.uuid4()
    pid = uuid.uuid4()
    h = _unit(uuid.uuid4(), pid)
    h.state = _state(is_shaken=True)
    st = _state(is_shaken=True)
    u = _unit(uid, pid, state=st, attached_heroes=[h])
    g = _game_with_unit(u)
    session = AsyncMock()
    with patch.object(us_mod, "log_event", new=AsyncMock()):
        await us_mod.apply_update_unit_state(
            session, g, u, uid, UpdateUnitStateRequest(is_shaken=False)
        )
    assert h.state.is_shaken is False


@pytest.mark.asyncio
async def test_fatigued_logs():
    uid = uuid.uuid4()
    pid = uuid.uuid4()
    st = _state(is_fatigued=False)
    u = _unit(uid, pid, state=st)
    g = _game_with_unit(u)
    session = AsyncMock()
    with patch.object(us_mod, "log_event", new=AsyncMock()) as le:
        await us_mod.apply_update_unit_state(
            session, g, u, uid, UpdateUnitStateRequest(is_fatigued=True)
        )
    assert st.is_fatigued is True
    assert le.await_args_list[0].args[2] == EventType.STATUS_FATIGUED


@pytest.mark.asyncio
async def test_deploy_from_ambush():
    uid = uuid.uuid4()
    pid = uuid.uuid4()
    st = _state(deployment_status=DeploymentStatus.IN_AMBUSH)
    u = _unit(uid, pid, state=st)
    g = _game_with_unit(u)
    session = AsyncMock()
    with patch.object(us_mod, "log_event", new=AsyncMock()) as le:
        await us_mod.apply_update_unit_state(
            session, g, u, uid, UpdateUnitStateRequest(deployment_status=DeploymentStatus.DEPLOYED)
        )
    assert le.await_args_list[0].args[2] == EventType.UNIT_DEPLOYED


@pytest.mark.asyncio
async def test_destroyed_detaches_hero_and_transport_passengers():
    uid = uuid.uuid4()
    pid = uuid.uuid4()
    hero = _unit(uuid.uuid4(), pid)
    hero.state = _state(is_shaken=False)
    st = _state(is_shaken=True, deployment_status=DeploymentStatus.DEPLOYED)
    u = _unit(uid, pid, state=st, attached_heroes=[hero])
    g = _game_with_unit(u)
    # passenger in another "player"
    p2 = MagicMock()
    pu = _unit(uuid.uuid4(), uuid.uuid4())
    pu.state = _state(transport_id=uid, deployment_status=DeploymentStatus.EMBARKED)
    p2.units = [pu]
    g.players = [g.players[0], p2]
    u.is_transport = True
    session = AsyncMock()

    with patch.object(us_mod, "log_event", new=AsyncMock()):
        await us_mod.apply_update_unit_state(
            session, g, u, uid, UpdateUnitStateRequest(deployment_status=DeploymentStatus.DESTROYED)
        )
    assert hero.attached_to_unit_id is None
    assert pu.state.transport_id is None
    assert pu.state.deployment_status == DeploymentStatus.DEPLOYED


@pytest.mark.asyncio
async def test_transport_embark_and_disembark_via_model_fields_set():
    uid = uuid.uuid4()
    pid = uuid.uuid4()
    tid = uuid.uuid4()
    st = _state(transport_id=None)
    u = _unit(uid, pid, state=st)
    g = _game_with_unit(u)
    session = AsyncMock()

    with patch.object(us_mod, "log_event", new=AsyncMock()):
        r1 = UpdateUnitStateRequest(transport_id=tid)
        await us_mod.apply_update_unit_state(session, g, u, uid, r1)
    assert st.transport_id == tid
    assert st.deployment_status == DeploymentStatus.EMBARKED

    with patch.object(us_mod, "log_event", new=AsyncMock()):
        r2 = UpdateUnitStateRequest.model_validate({"transport_id": None})
        await us_mod.apply_update_unit_state(session, g, u, uid, r2)
    assert st.transport_id is None


@pytest.mark.asyncio
async def test_spell_tokens_gain_and_spend():
    uid = uuid.uuid4()
    pid = uuid.uuid4()
    st = _state(spell_tokens=2)
    u = _unit(uid, pid, state=st)
    g = _game_with_unit(u)
    session = AsyncMock()
    with patch.object(us_mod, "log_event", new=AsyncMock()) as le:
        await us_mod.apply_update_unit_state(
            session, g, u, uid, UpdateUnitStateRequest(spell_tokens=5)
        )
    assert le.await_args_list[0].args[2] == EventType.SPELL_TOKENS_GAINED
    with patch.object(us_mod, "log_event", new=AsyncMock()) as le2:
        await us_mod.apply_update_unit_state(
            session, g, u, uid, UpdateUnitStateRequest(spell_tokens=1)
        )
    assert le2.await_args_list[0].args[2] == EventType.SPELL_TOKENS_SPENT


@pytest.mark.asyncio
async def test_limited_weapon_used():
    uid = uuid.uuid4()
    pid = uuid.uuid4()
    st = _state(limited_weapons_used=[])
    u = _unit(uid, pid, state=st)
    g = _game_with_unit(u)
    session = AsyncMock()
    with patch.object(us_mod, "log_event", new=AsyncMock()) as le:
        await us_mod.apply_update_unit_state(
            session, g, u, uid, UpdateUnitStateRequest(limited_weapons_used=["Bazooka"])
        )
    assert le.await_args_list[0].args[2] == EventType.LIMITED_WEAPON_USED


@pytest.mark.asyncio
async def test_models_remaining_update():
    uid = uuid.uuid4()
    pid = uuid.uuid4()
    st = _state(models_remaining=3)
    u = _unit(uid, pid, state=st)
    g = _game_with_unit(u)
    session = AsyncMock()
    await us_mod.apply_update_unit_state(
        session, g, u, uid, UpdateUnitStateRequest(models_remaining=1)
    )
    assert st.models_remaining == 1


@pytest.mark.asyncio
async def test_custom_notes():
    uid = uuid.uuid4()
    pid = uuid.uuid4()
    st = _state()
    u = _unit(uid, pid, state=st)
    g = _game_with_unit(u)
    session = AsyncMock()
    await us_mod.apply_update_unit_state(
        session, g, u, uid, UpdateUnitStateRequest(custom_notes="x")
    )
    assert st.custom_notes == "x"
