"""Branch coverage for app.api.game_helpers helpers."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.api import game_helpers as gh
from app.models.game import GameStatus


def test_utc_dt_none_and_naive_and_aware():
    assert gh._utc_dt(None) is None
    naive = datetime(2024, 6, 1, 12, 0, 0)
    u = gh._utc_dt(naive)
    assert u.tzinfo == timezone.utc
    aware = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert gh._utc_dt(aware).tzinfo == timezone.utc


def test_check_expiration_already_expired():
    g = SimpleNamespace(status=GameStatus.EXPIRED, is_solo=False, players=[], last_activity_at=None)
    assert gh.check_and_update_expiration(g) is True


def test_check_expiration_solo_inactive_30d():
    now = datetime(2025, 2, 15, tzinfo=timezone.utc)
    old = now - timedelta(days=31)
    g = SimpleNamespace(
        status=GameStatus.IN_PROGRESS,
        is_solo=True,
        players=[],
        last_activity_at=old,
    )
    with patch("app.api.game_helpers.datetime") as dm:
        dm.now = lambda tz=None: now
        dm.timedelta = timedelta
        dm.timezone = timezone
        assert gh.check_and_update_expiration(g) is True
        assert g.status == GameStatus.EXPIRED


def test_check_expiration_multi_all_disconnected_hour():
    now = datetime(2025, 2, 15, tzinfo=timezone.utc)
    old = now - timedelta(hours=2)
    p1 = SimpleNamespace(is_connected=False)
    p2 = SimpleNamespace(is_connected=False)
    g = SimpleNamespace(
        status=GameStatus.IN_PROGRESS,
        is_solo=False,
        players=[p1, p2],
        last_activity_at=old,
    )
    with patch("app.api.game_helpers.datetime") as dm:
        dm.now = lambda tz=None: now
        dm.timedelta = timedelta
        dm.timezone = timezone
        assert gh.check_and_update_expiration(g) is True
        assert g.status == GameStatus.EXPIRED


def test_check_expiration_multi_some_connected():
    now = datetime(2025, 2, 15, tzinfo=timezone.utc)
    old = now - timedelta(hours=2)
    g = SimpleNamespace(
        status=GameStatus.IN_PROGRESS,
        is_solo=False,
        players=[SimpleNamespace(is_connected=True)],
        last_activity_at=old,
    )
    with patch("app.api.game_helpers.datetime") as dm:
        dm.now = lambda tz=None: now
        dm.timedelta = timedelta
        dm.timezone = timezone
        assert gh.check_and_update_expiration(g) is False


@pytest.mark.asyncio
async def test_broadcast_if_not_solo_skips_solo():
    g = SimpleNamespace(is_solo=True)
    with patch("app.api.game_helpers.broadcast_to_game", new=AsyncMock()) as m:
        await gh.broadcast_if_not_solo(g, "AB", {})
        m.assert_not_awaited()
