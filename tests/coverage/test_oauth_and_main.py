"""Coverage for ``auth.oauth`` and ``main`` helpers (mocked request / env)."""

import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from litestar import Request
from litestar.exceptions import NotAuthorizedException

import app.auth.oauth as oauth_mod


def _req(
    *,
    scheme="https",
    host="example.com",
    port=443,
    path="/admin",
    query=None,
    cookies=None,
):
    url = MagicMock()
    url.scheme = scheme
    url.hostname = host
    url.port = port
    url.path = path
    r = MagicMock(spec=Request)
    r.url = url
    r.query_params = query or {}
    r.cookies = cookies or {}
    return r


def test_get_redirect_uri_production_host():
    r = _req(host="app.otahak.com", port=443, scheme="https")
    assert oauth_mod.get_redirect_uri(r).endswith("/admin/callback")


def test_get_redirect_uri_localhost_with_port():
    r = _req(host="localhost", port=8000, scheme="http")
    assert ":8000" in oauth_mod.get_redirect_uri(r)


def test_get_redirect_uri_localhost_default_port():
    r = _req(host="localhost", port=80, scheme="http")
    uri = oauth_mod.get_redirect_uri(r)
    assert "/admin/callback" in uri


@pytest.mark.asyncio
async def test_get_oauth_client_builds_client():
    r = _req()
    with patch.object(oauth_mod, "GOOGLE_CLIENT_ID", "id"), patch.object(
        oauth_mod, "GOOGLE_CLIENT_SECRET", "sec"
    ):
        c = await oauth_mod.get_oauth_client(r)
        assert c.client_id == "id"


@pytest.mark.asyncio
async def test_admin_login_oauth_not_configured():
    r = _req()
    with patch.object(oauth_mod, "GOOGLE_CLIENT_ID", None):
        with pytest.raises(NotAuthorizedException, match="OAuth not configured"):
            await oauth_mod.admin_login(r)


@pytest.mark.asyncio
async def test_admin_login_creates_authorization_url():
    r = _req(cookies={})
    fake_client = MagicMock()
    fake_client.create_authorization_url = MagicMock(
        return_value=("https://accounts.google.com/auth", None)
    )
    store = MagicMock()
    store.set = AsyncMock()
    with patch.object(oauth_mod, "GOOGLE_CLIENT_ID", "x"), patch.object(
        oauth_mod, "GOOGLE_CLIENT_SECRET", "y"
    ), patch.object(oauth_mod, "get_oauth_client", new=AsyncMock(return_value=fake_client)), patch.object(
        oauth_mod, "session_store", store
    ):
        resp = await oauth_mod.admin_login(r)
        assert resp.status_code in (301, 302, 303, 307, 308)


@pytest.mark.asyncio
async def test_admin_login_uses_existing_session_cookie():
    r = _req(cookies={"session_id": "existing-sid"})
    fake_client = MagicMock()
    fake_client.create_authorization_url = MagicMock(
        return_value=("https://accounts.google.com/auth", None)
    )
    store = MagicMock()
    store.set = AsyncMock()
    with patch.object(oauth_mod, "GOOGLE_CLIENT_ID", "x"), patch.object(
        oauth_mod, "GOOGLE_CLIENT_SECRET", "y"
    ), patch.object(oauth_mod, "get_oauth_client", new=AsyncMock(return_value=fake_client)), patch.object(
        oauth_mod, "session_store", store
    ):
        resp = await oauth_mod.admin_login(r)
        assert resp.status_code in (301, 302, 303, 307, 308)


@pytest.mark.asyncio
async def test_admin_login_invalid_auth_url():
    r = _req()
    fake_client = MagicMock()
    fake_client.create_authorization_url = MagicMock(return_value=("not-http", None))
    store = MagicMock()
    store.set = AsyncMock()
    with patch.object(oauth_mod, "GOOGLE_CLIENT_ID", "x"), patch.object(
        oauth_mod, "GOOGLE_CLIENT_SECRET", "y"
    ), patch.object(oauth_mod, "get_oauth_client", new=AsyncMock(return_value=fake_client)), patch.object(
        oauth_mod, "session_store", store
    ):
        with pytest.raises(NotAuthorizedException, match="Invalid OAuth"):
            await oauth_mod.admin_login(r)


@pytest.mark.asyncio
async def test_admin_login_create_url_raises():
    r = _req()
    fake_client = MagicMock()
    fake_client.create_authorization_url = MagicMock(side_effect=RuntimeError("nope"))
    store = MagicMock()
    store.set = AsyncMock()
    with patch.object(oauth_mod, "GOOGLE_CLIENT_ID", "x"), patch.object(
        oauth_mod, "GOOGLE_CLIENT_SECRET", "y"
    ), patch.object(oauth_mod, "get_oauth_client", new=AsyncMock(return_value=fake_client)), patch.object(
        oauth_mod, "session_store", store
    ):
        with pytest.raises(NotAuthorizedException, match="Failed to create OAuth URL"):
            await oauth_mod.admin_login(r)


@pytest.mark.asyncio
async def test_admin_login_outer_exception():
    r = _req()
    with patch.object(oauth_mod, "GOOGLE_CLIENT_ID", "x"), patch.object(
        oauth_mod, "GOOGLE_CLIENT_SECRET", "y"
    ), patch.object(oauth_mod, "get_oauth_client", new=AsyncMock(side_effect=RuntimeError("bad"))):
        with pytest.raises(RuntimeError, match="bad"):
            await oauth_mod.admin_login(r)


@pytest.mark.asyncio
async def test_admin_callback_error_param():
    r = _req(query={"error": "access_denied"})
    resp = await oauth_mod.admin_callback(r)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_admin_callback_missing_code_state():
    r = _req(query={})
    resp = await oauth_mod.admin_callback(r)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_admin_callback_no_session_cookie():
    r = _req(query={"code": "c", "state": "s"}, cookies={})
    resp = await oauth_mod.admin_callback(r)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_admin_callback_no_stored_state():
    r = _req(query={"code": "c", "state": "s"}, cookies={"session_id": "sid"})
    store = MagicMock()
    store.get = AsyncMock(return_value=None)
    with patch.object(oauth_mod, "session_store", store):
        resp = await oauth_mod.admin_callback(r)
        assert resp.status_code == 400


@pytest.mark.asyncio
async def test_admin_callback_state_mismatch():
    r = _req(query={"code": "c", "state": "recv"}, cookies={"session_id": "sid"})
    store = MagicMock()
    store.get = AsyncMock(return_value="stored")
    with patch.object(oauth_mod, "session_store", store):
        resp = await oauth_mod.admin_callback(r)
        assert resp.status_code == 400


@pytest.mark.asyncio
async def test_admin_callback_state_bytes_and_success(monkeypatch):
    r = _req(query={"code": "c", "state": "ok"}, cookies={"session_id": "sid"})
    store = {
        "oauth_state:sid": b"ok",
        f"{oauth_mod.ADMIN_SESSION_KEY}:sid": None,
    }

    async def getter(key):
        return store.get(key)

    async def setter(key, val, expires_in=None):
        store[key] = val

    async def deleter(key):
        store.pop(key, None)

    token_resp = {"access_token": "tok"}
    user_resp = MagicMock()
    user_resp.json = MagicMock(return_value={"email": oauth_mod.GOOGLE_AUTHORIZED_EMAIL})

    class CM:
        async def __aenter__(self):
            self.c = MagicMock()
            self.c.fetch_token = AsyncMock(return_value=token_resp)
            self.c.get = AsyncMock(return_value=user_resp)
            return self.c

        async def __aexit__(self, *a):
            return False

    st = MagicMock()
    st.get = AsyncMock(side_effect=getter)
    st.set = AsyncMock(side_effect=setter)
    st.delete = AsyncMock(side_effect=deleter)
    with patch.object(oauth_mod, "session_store", st), patch.object(
        oauth_mod, "GOOGLE_CLIENT_ID", "id"
    ), patch.object(oauth_mod, "GOOGLE_CLIENT_SECRET", "sec"), patch(
        "app.auth.oauth.AsyncOAuth2Client", return_value=CM()
    ):
        resp = await oauth_mod.admin_callback(r)
        assert resp.status_code in (301, 302, 303, 307, 308)


@pytest.mark.asyncio
async def test_admin_callback_wrong_email():
    r = _req(query={"code": "c", "state": "ok"}, cookies={"session_id": "sid"})

    async def getter(key):
        if key == "oauth_state:sid":
            return "ok"
        return None

    token_resp = {"access_token": "tok"}
    user_resp = MagicMock()
    user_resp.json = MagicMock(return_value={"email": "other@x.com"})

    class CM:
        async def __aenter__(self):
            self.c = MagicMock()
            self.c.fetch_token = AsyncMock(return_value=token_resp)
            self.c.get = AsyncMock(return_value=user_resp)
            return self.c

        async def __aexit__(self, *a):
            return False

    st = MagicMock()
    st.get = AsyncMock(side_effect=getter)
    with patch.object(oauth_mod, "session_store", st), patch.object(
        oauth_mod, "GOOGLE_CLIENT_ID", "id"
    ), patch.object(oauth_mod, "GOOGLE_CLIENT_SECRET", "sec"), patch(
        "app.auth.oauth.AsyncOAuth2Client", return_value=CM()
    ):
        resp = await oauth_mod.admin_callback(r)
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_callback_token_exchange_fails():
    r = _req(query={"code": "c", "state": "ok"}, cookies={"session_id": "sid"})

    async def getter(key):
        if key == "oauth_state:sid":
            return "ok"
        return None

    class CM:
        async def __aenter__(self):
            raise RuntimeError("fail")

        async def __aexit__(self, *a):
            return False

    st = MagicMock()
    st.get = AsyncMock(side_effect=getter)
    with patch.object(oauth_mod, "session_store", st), patch.object(
        oauth_mod, "GOOGLE_CLIENT_ID", "id"
    ), patch.object(oauth_mod, "GOOGLE_CLIENT_SECRET", "sec"), patch(
        "app.auth.oauth.AsyncOAuth2Client", return_value=CM()
    ):
        resp = await oauth_mod.admin_callback(r)
        assert resp.status_code == 500


@pytest.mark.asyncio
async def test_admin_callback_no_access_token_in_response():
    r = _req(query={"code": "c", "state": "ok"}, cookies={"session_id": "sid"})

    async def getter(key):
        if key == "oauth_state:sid":
            return "ok"
        return None

    token_resp = {}
    user_resp = MagicMock()

    class CM:
        async def __aenter__(self):
            self.c = MagicMock()
            self.c.fetch_token = AsyncMock(return_value=token_resp)
            self.c.get = AsyncMock(return_value=user_resp)
            return self.c

        async def __aexit__(self, *a):
            return False

    st = MagicMock()
    st.get = AsyncMock(side_effect=getter)
    with patch.object(oauth_mod, "session_store", st), patch.object(
        oauth_mod, "GOOGLE_CLIENT_ID", "id"
    ), patch.object(oauth_mod, "GOOGLE_CLIENT_SECRET", "sec"), patch(
        "app.auth.oauth.AsyncOAuth2Client", return_value=CM()
    ):
        resp = await oauth_mod.admin_callback(r)
        assert resp.status_code == 500


@pytest.mark.asyncio
async def test_admin_logout_and_is_admin_authenticated():
    sid = "s1"
    r = _req(cookies={"session_id": sid})
    st = MagicMock()
    st.delete = AsyncMock()
    with patch.object(oauth_mod, "session_store", st):
        resp = await oauth_mod.admin_logout(r)
        assert st.delete.await_count >= 1
        assert resp.status_code in (301, 302, 303, 307, 308)

    assert await oauth_mod.is_admin_authenticated(_req(cookies={})) is False

    async def auth_get(key):
        if oauth_mod.ADMIN_SESSION_KEY in key:
            return "true"
        if oauth_mod.ADMIN_EMAIL_KEY in key:
            return oauth_mod.GOOGLE_AUTHORIZED_EMAIL
        return None

    r2 = _req(cookies={"session_id": sid})
    st2 = MagicMock()
    st2.get = AsyncMock(side_effect=auth_get)
    with patch.object(oauth_mod, "session_store", st2):
        assert await oauth_mod.is_admin_authenticated(r2) is True

    r3 = _req(cookies={"session_id": sid})
    st3 = MagicMock()
    st3.get = AsyncMock(side_effect=lambda k: b"true" if oauth_mod.ADMIN_SESSION_KEY in k else None)
    with patch.object(oauth_mod, "session_store", st3):
        assert await oauth_mod.is_admin_authenticated(r3) is False

    r4 = _req(cookies={"session_id": "s2"})
    st4 = MagicMock()
    st4.get = AsyncMock(
        side_effect=lambda k: None if oauth_mod.ADMIN_SESSION_KEY in k else oauth_mod.GOOGLE_AUTHORIZED_EMAIL
    )
    with patch.object(oauth_mod, "session_store", st4):
        assert await oauth_mod.is_admin_authenticated(r4) is False

    authorized = oauth_mod.GOOGLE_AUTHORIZED_EMAIL
    r5 = _req(cookies={"session_id": "s3"})
    st5 = MagicMock()
    st5.get = AsyncMock(
        side_effect=lambda k: (
            b"1"
            if oauth_mod.ADMIN_SESSION_KEY in k
            else (authorized.encode("utf-8") if oauth_mod.ADMIN_EMAIL_KEY in k else None)
        )
    )
    with patch.object(oauth_mod, "session_store", st5):
        assert await oauth_mod.is_admin_authenticated(r5) is True


@pytest.mark.asyncio
async def test_require_admin_guard_paths():
    from litestar.handlers.base import BaseRouteHandler

    h = MagicMock(spec=BaseRouteHandler)
    conn = MagicMock()
    conn.url.path = "/api/admin/stats"
    conn.cookies = {}

    with pytest.raises(NotAuthorizedException):
        await oauth_mod.require_admin_guard(conn, h)

    sid = str(uuid.uuid4())
    conn.cookies = {"session_id": sid}

    async def g(key):
        if oauth_mod.ADMIN_SESSION_KEY in key:
            return "true"
        if oauth_mod.ADMIN_EMAIL_KEY in key:
            return oauth_mod.GOOGLE_AUTHORIZED_EMAIL
        return None

    sg = MagicMock()
    sg.get = AsyncMock(side_effect=g)
    with patch.object(oauth_mod, "session_store", sg):
        await oauth_mod.require_admin_guard(conn, h)

    async def g2(key):
        if oauth_mod.ADMIN_SESSION_KEY in key:
            return "true"
        return None

    conn2 = MagicMock()
    conn2.url.path = "/api/admin/x"
    conn2.cookies = {"session_id": sid}
    sg2 = MagicMock()
    sg2.get = AsyncMock(side_effect=g2)
    with patch.object(oauth_mod, "session_store", sg2):
        with pytest.raises(NotAuthorizedException, match="Unauthorized"):
            await oauth_mod.require_admin_guard(conn2, h)

    async def g3(key):
        if oauth_mod.ADMIN_SESSION_KEY in key:
            return "true"
        if oauth_mod.ADMIN_EMAIL_KEY in key:
            return b"wrong@email.com"
        return None

    conn3 = MagicMock()
    conn3.url.path = "/api/admin/x"
    conn3.cookies = {"session_id": sid}
    sg3 = MagicMock()
    sg3.get = AsyncMock(side_effect=g3)
    with patch.object(oauth_mod, "session_store", sg3):
        with pytest.raises(NotAuthorizedException):
            await oauth_mod.require_admin_guard(conn3, h)

    conn4 = MagicMock()
    conn4.url.path = "/api/admin/x"
    conn4.cookies = {"session_id": sid}
    sg4 = MagicMock()
    sg4.get = AsyncMock(return_value=None)
    with patch.object(oauth_mod, "session_store", sg4):
        with pytest.raises(NotAuthorizedException, match="Not authenticated"):
            await oauth_mod.require_admin_guard(conn4, h)


def test_log_oauth_credentials_both_branches(monkeypatch, caplog):
    import logging

    caplog.set_level(logging.WARNING)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "")
    oauth_mod._log_oauth_credentials_at_import()
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "a")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "b")
    caplog.set_level(logging.INFO)
    oauth_mod._log_oauth_credentials_at_import()


def test_main_conditional_print_and_handlers():
    import app.main as main_mod
    from litestar import Request
    from litestar.exceptions import HTTPException, NotAuthorizedException

    main_mod._conditional_print("x")

    req = MagicMock(spec=Request)
    req.url.path = "/api/x"
    req.url = MagicMock(path="/api/x")
    req.method = "GET"
    req.headers = {}

    he = HTTPException("bad", status_code=418)
    resp = main_mod.log_exceptions(req, he)
    assert resp.status_code == 418

    with patch.object(main_mod, "DEBUG", True):
        resp2 = main_mod.log_exceptions(req, RuntimeError("e"))
        assert resp2.status_code == 500

    with patch.object(main_mod, "DEBUG", False):
        resp3 = main_mod.log_exceptions(req, RuntimeError("e"))
        assert resp3.status_code == 500

    req2 = MagicMock()
    req2.url.path = "/api/admin/stats"
    r4 = main_mod.handle_auth_exception(req2, NotAuthorizedException("n"))
    assert r4.status_code == 401

    req3 = MagicMock()
    req3.url.path = "/admin"
    r5 = main_mod.handle_auth_exception(req3, NotAuthorizedException("n"))
    assert r5.status_code in (301, 302, 303, 307, 308)

    req4 = MagicMock()
    req4.url.path = "/other"
    r6 = main_mod.handle_auth_exception(req4, NotAuthorizedException("n"))
    assert r6.status_code == 401


@pytest.mark.asyncio
async def test_main_register_template_globals():
    import app.main as main_mod

    eng = MagicMock()
    main_mod.register_template_globals(eng)
    assert eng.register_template_callable.call_count >= 2
    calls = eng.register_template_callable.call_args_list
    names = [c.args[0] for c in calls]
    by_name = dict(zip(names, [c.args[1] for c in calls], strict=True))
    assert by_name["get_base_path"]({}) == ""
    req = MagicMock()
    eng2 = MagicMock()
    with patch("app.utils.get_base_path", side_effect=RuntimeError("x")):
        main_mod.register_template_globals(eng2)
        h = eng2.register_template_callable.call_args_list[0].args[1]
        assert h({"request": req}) == ""
    eng3 = MagicMock()
    with patch("app.utils.get_base_path", return_value="/bp"):
        main_mod.register_template_globals(eng3)
        h2 = eng3.register_template_callable.call_args_list[0].args[1]
        assert h2({"request": req}) == "/bp"
    assert by_name["APP_DEBUG"]({}) is main_mod.DEBUG


def test_main_load_env_file_fallback_quotes_and_skip_existing(tmp_path, monkeypatch):
    import app.main as main_mod

    p = tmp_path / ".env"
    p.write_text('FOO="bar"\nBAZ=\'qux\'\n')
    monkeypatch.setattr(main_mod, "ENV_FILE_PATHS", [p])
    monkeypatch.delenv("FOO", raising=False)
    monkeypatch.delenv("BAZ", raising=False)
    assert main_mod.load_env_file_fallback() is True
    assert os.environ["FOO"] == "bar"
    assert os.environ["BAZ"] == "qux"
    monkeypatch.setenv("FOO", "already")
    assert main_mod.load_env_file_fallback() is True


def test_main_load_env_file_fallback_loaded_print_and_io_error(tmp_path, monkeypatch):
    import app.main as main_mod

    p = tmp_path / ".env"
    p.write_text("A=1\n")
    monkeypatch.setattr(main_mod, "ENV_FILE_PATHS", [p])
    monkeypatch.delenv("A", raising=False)
    with patch.object(main_mod, "DEBUG", True), patch.object(main_mod, "_conditional_print") as pr:
        assert main_mod.load_env_file_fallback() is True
        pr.assert_called()

    def boom(*_a, **_k):
        raise OSError("nope")

    monkeypatch.delenv("A", raising=False)
    with patch.object(main_mod, "DEBUG", True), patch.object(main_mod, "_conditional_print"), patch(
        "builtins.open", boom
    ):
        assert main_mod.load_env_file_fallback() is False


def test_main_ensure_oauth_from_dotenv_branches(tmp_path, monkeypatch):
    import app.main as main_mod

    empty = tmp_path / "empty.env"
    empty.write_text("# nothing\n")
    monkeypatch.setattr(main_mod, "ENV_FILE_PATHS", [empty])
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    with patch.object(main_mod, "_conditional_print") as pr:
        main_mod.ensure_oauth_from_dotenv()
        assert any("still not found" in str(c.args[0]) for c in pr.call_args_list)

    good = tmp_path / "good.env"
    good.write_text("GOOGLE_CLIENT_ID=x\nGOOGLE_CLIENT_SECRET=y\n")
    monkeypatch.setattr(main_mod, "ENV_FILE_PATHS", [good])
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    with patch.object(main_mod, "_conditional_print") as pr:
        main_mod.ensure_oauth_from_dotenv()
        assert os.environ["GOOGLE_CLIENT_ID"] == "x"
        assert os.environ["GOOGLE_CLIENT_SECRET"] == "y"
        assert any("loaded from .env" in str(c.args[0]) for c in pr.call_args_list)


def test_main_log_google_oauth_env_status():
    import app.main as main_mod

    with patch.object(main_mod, "logger") as log:
        main_mod.log_google_oauth_env_status("a", "b")
        log.info.assert_called()
        log.debug.assert_called()
    with patch.object(main_mod, "logger") as log:
        main_mod.log_google_oauth_env_status(None, "b")
        assert log.warning.call_count >= 3


@pytest.mark.asyncio
async def test_main_run_startup_migrations_branches(monkeypatch):
    import app.main as main_mod
    from litestar import Litestar

    app = MagicMock(spec=Litestar)
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    await main_mod.run_startup_migrations(app)

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h/d")
    monkeypatch.setenv("AUTO_RUN_MIGRATIONS", "false")
    await main_mod.run_startup_migrations(app)

    monkeypatch.setenv("AUTO_RUN_MIGRATIONS", "true")

    class _FakeScript:
        exists_val = False

        def exists(self):
            return _FakeScript.exists_val

        def __str__(self):
            return "/tmp/run_pending_migrations.py"

    script = _FakeScript()
    deploy_dir = MagicMock()
    deploy_dir.__truediv__ = lambda _self, name: script if name == "run_pending_migrations.py" else deploy_dir
    root = MagicMock()
    root.__truediv__ = lambda _self, name: deploy_dir if name == "deploy" else root
    app_dir = MagicMock()
    app_dir.parent = root
    file_p = MagicMock()
    file_p.parent = app_dir

    with patch("pathlib.Path", return_value=file_p):
        await main_mod.run_startup_migrations(app)

    _FakeScript.exists_val = True
    with patch("pathlib.Path", return_value=file_p), patch.object(
        main_mod.subprocess, "run", return_value=MagicMock(returncode=0, stdout="done", stderr="")
    ):
        await main_mod.run_startup_migrations(app)

    with patch.object(main_mod, "DEBUG", True), patch("pathlib.Path", return_value=file_p), patch.object(
        main_mod.subprocess, "run", return_value=MagicMock(returncode=0, stdout="done", stderr="")
    ):
        await main_mod.run_startup_migrations(app)

    with patch("pathlib.Path", return_value=file_p), patch.object(
        main_mod.subprocess, "run", return_value=MagicMock(returncode=1, stdout="", stderr="err")
    ):
        await main_mod.run_startup_migrations(app)

    with patch("pathlib.Path", return_value=file_p), patch.object(
        main_mod.subprocess, "run", side_effect=main_mod.subprocess.TimeoutExpired("cmd", 60)
    ):
        await main_mod.run_startup_migrations(app)

    with patch("pathlib.Path", return_value=file_p), patch.object(
        main_mod.subprocess, "run", side_effect=RuntimeError("x")
    ):
        await main_mod.run_startup_migrations(app)
