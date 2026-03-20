"""Admin API, feedback POST, and log_request_error exception swallow."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError, ProgrammingError

import app.auth.oauth as oauth


def _admin_store():
    async def store_get(key):
        if oauth.ADMIN_SESSION_KEY in key:
            return "true"
        if oauth.ADMIN_EMAIL_KEY in key:
            return oauth.GOOGLE_AUTHORIZED_EMAIL
        return None

    st = MagicMock()
    st.get = AsyncMock(side_effect=store_get)
    return st


@pytest.mark.asyncio
async def test_submit_feedback_persists(client):
    r = await client.post(
        "/api/feedback/",
        json={"name": "N", "email": "n@example.com", "message": "Hello feedback"},
    )
    assert r.status_code in (200, 201)
    body = r.json()
    assert body.get("success") is True


@pytest.mark.asyncio
async def test_admin_feedback_list_and_filters(client):
    st = _admin_store()
    cookies = {"session_id": "adm1"}
    with patch.object(oauth, "session_store", st):
        await client.post(
            "/api/feedback/",
            json={"name": "A", "email": "a@ex.com", "message": "m1"},
        )
        r = await client.get("/api/admin/feedback", cookies=cookies)
        assert r.status_code == 200
        items = r.json()
        assert isinstance(items, list) and len(items) >= 1

        r2 = await client.get("/api/admin/feedback?unread_only=true", cookies=cookies)
        assert r2.status_code == 200


@pytest.mark.asyncio
async def test_admin_mark_read_and_delete_feedback(client):
    st = _admin_store()
    cookies = {"session_id": "adm2"}
    with patch.object(oauth, "session_store", st):
        await client.post(
            "/api/feedback/",
            json={"name": "B", "email": "b@ex.com", "message": "to delete"},
        )
        lst = (await client.get("/api/admin/feedback", cookies=cookies)).json()
        fid = uuid.UUID(lst[0]["id"])

        r_patch = await client.patch(f"/api/admin/feedback/{fid}/read", cookies=cookies)
        assert r_patch.status_code == 200
        assert r_patch.json().get("success") is True

        bad = uuid.uuid4()
        r_miss = await client.patch(f"/api/admin/feedback/{bad}/read", cookies=cookies)
        assert r_miss.json().get("success") is False

        r_del = await client.delete(f"/api/admin/feedback/{fid}", cookies=cookies)
        assert r_del.status_code == 200

        r_nf = await client.delete(f"/api/admin/feedback/{uuid.uuid4()}", cookies=cookies)
        assert r_nf.status_code == 404


@pytest.mark.asyncio
async def test_admin_get_feedback_db_errors(client):
    st = _admin_store()
    cookies = {"session_id": "adm3"}
    with patch.object(oauth, "session_store", st):
        with patch(
            "sqlalchemy.ext.asyncio.AsyncSession.execute",
            new=AsyncMock(side_effect=ProgrammingError("stmt", {}, Exception("no such table: feedback"))),
        ):
            r = await client.get("/api/admin/feedback", cookies=cookies)
            assert r.status_code == 500

        with patch(
            "sqlalchemy.ext.asyncio.AsyncSession.execute",
            new=AsyncMock(side_effect=OperationalError("stmt", {}, Exception("disk full"))),
        ):
            r2 = await client.get("/api/admin/feedback", cookies=cookies)
            assert r2.status_code == 500


@pytest.mark.asyncio
async def test_admin_get_feedback_generic_error(client):
    st = _admin_store()
    with patch.object(oauth, "session_store", st):
        with patch(
            "sqlalchemy.ext.asyncio.AsyncSession.execute",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            r = await client.get("/api/admin/feedback", cookies={"session_id": "e1"})
            assert r.status_code == 500


@pytest.mark.asyncio
async def test_admin_stats_and_recent_events(client):
    st = _admin_store()
    cookies = {"session_id": "adm4"}
    with patch.object(oauth, "session_store", st):
        r = await client.get("/api/admin/stats", cookies=cookies)
        assert r.status_code == 200
        data = r.json()
        for k in (
            "total_games",
            "active_games",
            "total_players",
            "total_units",
            "total_feedback",
            "unread_feedback",
            "games_last_24h",
            "games_last_7d",
        ):
            assert k in data

        ev = await client.get("/api/admin/events/recent", cookies=cookies)
        assert ev.status_code == 200
        assert isinstance(ev.json(), list)


@pytest.mark.asyncio
async def test_admin_stats_db_errors(client):
    st = _admin_store()
    with patch.object(oauth, "session_store", st):
        with patch(
            "sqlalchemy.ext.asyncio.AsyncSession.execute",
            new=AsyncMock(side_effect=ProgrammingError("stmt", {}, Exception('relation "games" does not exist'))),
        ):
            r = await client.get("/api/admin/stats", cookies={"session_id": "s1"})
            assert r.status_code == 500
            err = r.json().get("detail") or r.json()
            assert isinstance(err, dict) or "migration" in str(err).lower()

        with patch(
            "sqlalchemy.ext.asyncio.AsyncSession.execute",
            new=AsyncMock(side_effect=OperationalError("stmt", {}, Exception("other"))),
        ):
            r2 = await client.get("/api/admin/stats", cookies={"session_id": "s2"})
            assert r2.status_code == 500

        with patch(
            "sqlalchemy.ext.asyncio.AsyncSession.execute",
            new=AsyncMock(side_effect=RuntimeError("weird")),
        ):
            r3 = await client.get("/api/admin/stats", cookies={"session_id": "s3"})
            assert r3.status_code == 500


@pytest.mark.asyncio
async def test_admin_delete_feedback_error_path(client):
    st = _admin_store()
    fake_id = uuid.uuid4()
    with patch.object(oauth, "session_store", st):
        with patch(
            "sqlalchemy.ext.asyncio.AsyncSession.execute",
            new=AsyncMock(side_effect=RuntimeError("delete failed")),
        ):
            r = await client.delete(f"/api/admin/feedback/{fake_id}", cookies={"session_id": "d1"})
            assert r.status_code == 500


def test_log_request_error_swallows_bad_request_extraction(monkeypatch):
    monkeypatch.setenv("APP_DEBUG", "true")
    import importlib

    import app.utils.logging as lg

    importlib.reload(lg)

    class BadUrl:
        @property
        def path(self):
            raise RuntimeError("no path")

    req = MagicMock()
    req.url = BadUrl()
    req.method = "GET"
    req.headers = {}

    lg.log_request_error(req, ValueError("inner"))

    importlib.reload(lg)
