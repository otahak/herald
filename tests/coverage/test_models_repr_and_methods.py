"""Cover model __repr__ and small methods not hit by API tests."""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, PropertyMock, patch

from app.models.event import EventType, GameEvent
from app.models.feedback import Feedback
from app.models.game import Game, GameStatus, GameSystem
from app.models.game_save import GameSave
from app.models.objective import Objective, ObjectiveStatus
from app.models.player import Player
from app.models.unit import DeploymentStatus, Unit, UnitState


def test_game_event_repr_truncates_description():
    gid = uuid.uuid4()
    ev = GameEvent(
        game_id=gid,
        event_type=EventType.GAME_STARTED,
        description="x" * 80,
        round_number=2,
    )
    r = repr(ev)
    assert "R2" in r
    assert "game_started" in r


def test_feedback_repr():
    f = Feedback(name="n", email="a@b.co", message="hi")
    f.created_at = datetime(2024, 1, 2, tzinfo=timezone.utc)
    assert "a@b.co" in repr(f)


def test_game_repr():
    g = Game(name="G", code="AB12CD", game_system=GameSystem.GFF, status=GameStatus.IN_PROGRESS)
    assert "AB12CD" in repr(g) and "in_progress" in repr(g)


def test_game_save_repr():
    gid = uuid.uuid4()
    gs = GameSave(game_id=gid, save_name="slot1", game_state_json="{}")
    assert str(gid) in repr(gs) and "slot1" in repr(gs)


def test_objective_methods_and_display_name():
    gid = uuid.uuid4()
    pid = uuid.uuid4()
    o = Objective(game_id=gid, marker_number=3)
    assert o.display_name == "Objective 3"
    o.label = "Alpha"
    assert o.display_name == "Alpha"
    o.seize(pid)
    assert o.status == ObjectiveStatus.SEIZED
    assert o.controlled_by_id == pid
    o.contest()
    assert o.status == ObjectiveStatus.CONTESTED
    o.neutralize()
    assert o.status == ObjectiveStatus.NEUTRAL
    assert o.controlled_by_id is None
    assert "neutral" in repr(o)


def test_player_morale_and_health_properties():
    gid = uuid.uuid4()
    p = Player(game_id=gid, name="P1")

    destroyed = MagicMock()
    destroyed.state = MagicMock(is_destroyed=True)
    alive = MagicMock()
    alive.state = MagicMock(is_destroyed=False)
    bare = MagicMock(state=None)

    with patch.object(Player, "units", new_callable=PropertyMock, return_value=[destroyed, alive, bare]):
        assert p.current_unit_count == 2
        p.starting_unit_count = 4
        assert p.morale_threshold_reached is True

    p.starting_unit_count = 4
    with patch.object(Player, "units", new_callable=PropertyMock, return_value=[alive, alive, alive]):
        assert p.morale_threshold_reached is False

    p.starting_unit_count = 0
    with patch.object(Player, "units", new_callable=PropertyMock, return_value=[destroyed]):
        assert p.morale_threshold_reached is False
        assert p.army_health_percentage == 1.0

    p.starting_unit_count = 10
    with patch.object(Player, "units", new_callable=PropertyMock, return_value=[alive] * 5):
        assert p.army_health_percentage == 0.5
    assert "P1" in repr(p)


def test_unit_and_unit_state_properties():
    pid = uuid.uuid4()
    u = Unit(
        player_id=pid,
        name="Squad",
        quality=3,
        defense=4,
        size=2,
        tough=2,
        cost=10,
    )
    assert u.display_name == "Squad"
    u.custom_name = "Nick"
    assert u.display_name == "Nick"
    assert u.max_wounds == 4
    assert "Nick" in repr(u)

    uid = uuid.uuid4()
    st = UnitState(unit_id=uid, wounds_taken=1, models_remaining=2)
    st.unit = u
    assert st.wounds_remaining == 3
    assert 0 < st.health_percentage <= 1

    st2 = UnitState(unit_id=uuid.uuid4())
    st2.unit = None
    assert st2.wounds_remaining == 0
    assert st2.health_percentage == 1.0

    u0 = Unit(player_id=pid, name="Z", quality=4, defense=4, size=1, tough=1, cost=1)
    u0.size = 0
    st3 = UnitState(unit_id=uuid.uuid4())
    st3.unit = u0
    assert st3.health_percentage == 1.0

    st4 = UnitState(unit_id=uuid.uuid4(), deployment_status=DeploymentStatus.DESTROYED)
    assert st4.is_destroyed is True
    mock_u = MagicMock()
    mock_u.display_name = "U"
    st4.unit = mock_u
    assert "U" in repr(st4)


def test_unit_state_reset_for_new_round_grants_caster_tokens():
    u = MagicMock()
    u.is_caster = True
    u.caster_level = 2
    u.rules = []
    u.loadout = []
    u.upgrades = []

    class _St:
        pass

    st = _St()
    st.spell_tokens = 1
    st.activated_this_round = True
    st.is_fatigued = True
    st.unit = u
    UnitState.reset_for_new_round(st)
    assert st.spell_tokens == 3
    assert st.activated_this_round is False
    assert st.is_fatigued is False
