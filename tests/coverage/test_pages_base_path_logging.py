"""100% coverage: HTML routes, ``get_base_path``, logging helpers."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from litestar import Request


def _req_scope_root(path="/x", root="/herald"):
    u = MagicMock()
    u.path = path
    u.hostname = "testserver"
    u.scheme = "http"
    r = MagicMock(spec=Request)
    r.url = u
    r.scope = {"root_path": root}
    return r


def test_get_base_path_root_path():
    from app.utils import get_base_path

    assert get_base_path(_req_scope_root()) == "/herald"


def test_get_base_path_scope_access_error():
    from app.utils import get_base_path

    r = MagicMock(spec=Request)
    r.url = MagicMock(path="/", hostname=None)

    def bad_scope(_self):
        raise KeyError("x")

    type(r).scope = property(bad_scope)
    assert get_base_path(r) == ""


def test_get_base_path_base_path_env(monkeypatch):
    from app.utils import get_base_path

    monkeypatch.setenv("BASE_PATH", "/prefix/")
    r = MagicMock(spec=Request)
    r.url = MagicMock(path="/", hostname=None)
    r.scope = {}
    assert get_base_path(r) == "/prefix"


def test_get_base_path_from_admin_path(monkeypatch):
    from app.utils import get_base_path

    monkeypatch.delenv("BASE_PATH", raising=False)
    r = MagicMock(spec=Request)
    r.url = MagicMock(path="/prefix/admin/login", hostname=None)
    r.scope = {}
    assert get_base_path(r) == "/prefix"


def test_get_base_path_from_api_path(monkeypatch):
    from app.utils import get_base_path

    monkeypatch.delenv("BASE_PATH", raising=False)
    r = MagicMock(spec=Request)
    r.url = MagicMock(path="/app/api/games", hostname=None)
    r.scope = {}
    assert get_base_path(r) == "/app"


def test_get_base_path_from_game_prefix(monkeypatch):
    from app.utils import get_base_path

    monkeypatch.delenv("BASE_PATH", raising=False)
    r = MagicMock(spec=Request)
    r.url = MagicMock(path="/x/game/ABC", hostname=None)
    r.scope = {}
    assert get_base_path(r) == "/x"


def test_get_base_path_from_feedback_users(monkeypatch):
    from app.utils import get_base_path

    monkeypatch.delenv("BASE_PATH", raising=False)
    r = MagicMock(spec=Request)
    r.url = MagicMock(path="/z/feedback", hostname=None)
    r.scope = {}
    assert get_base_path(r) == "/z"
    r2 = MagicMock(spec=Request)
    r2.url = MagicMock(path="/z/users", hostname=None)
    r2.scope = {}
    assert get_base_path(r2) == "/z"


def test_get_base_path_production_root_hostname(monkeypatch):
    from app.utils import get_base_path

    monkeypatch.delenv("BASE_PATH", raising=False)
    r = MagicMock(spec=Request)
    r.url = MagicMock(path="/", hostname="app.otahak.com")
    r.scope = {}
    assert get_base_path(r) == "/herald"


def test_logging_debug_log_and_error_traceback(monkeypatch):
    import app.utils.logging as log_mod

    monkeypatch.setattr(log_mod, "DEBUG", True)
    with patch.object(log_mod.logger, "log") as lg:
        log_mod.debug_log("msg %s", "a", level=10)
        lg.assert_called()

    try:
        raise ValueError("e")
    except ValueError as exc:
        with patch.object(log_mod.logger, "error") as lg:
            log_mod.error_log("x", exc=exc, context={"k": "v"})
            lg.assert_called()


def test_log_request_error_inner_exception():
    import app.utils.logging as log_mod

    req = MagicMock()
    req.url = property(lambda _s: (_ for _ in ()).throw(RuntimeError("inner")))
    with patch.object(log_mod, "log_exception_with_context") as m:
        log_mod.log_request_error(req, ValueError("top"))
        m.assert_called_once()


@pytest.mark.parametrize(
    "path",
    ["/", "/users", "/help", "/feedback", "/game"],
)
def test_public_template_pages(sync_client, path):
    r = sync_client.get(path)
    assert r.status_code == 200


def test_game_board_template_non_admin(sync_client):
    with patch("app.game.routes.is_admin_authenticated", new=AsyncMock(return_value=False)):
        r = sync_client.get("/game/ab12")
    assert r.status_code == 200


def test_game_board_redirect_when_admin(sync_client):
    with patch("app.game.routes.is_admin_authenticated", new=AsyncMock(return_value=True)):
        r = sync_client.get("/game/ab12", follow_redirects=False)
    assert r.status_code in (301, 302, 303, 307, 308)


def test_admin_login_page(sync_client):
    r = sync_client.get("/admin/login-page")
    assert r.status_code == 200


def test_admin_dashboard_and_observe_with_session(sync_client):
    import app.auth.oauth as oauth

    sid = "adm-session-1"

    async def store_get(key):
        if oauth.ADMIN_SESSION_KEY in key:
            return "true"
        if oauth.ADMIN_EMAIL_KEY in key:
            return oauth.GOOGLE_AUTHORIZED_EMAIL
        return None

    st = MagicMock()
    st.get = AsyncMock(side_effect=store_get)

    with patch.object(oauth, "session_store", st):
        r = sync_client.get("/admin", cookies={"session_id": sid})
        assert r.status_code == 200
        r2 = sync_client.get("/admin/observe/xy99", cookies={"session_id": sid})
        assert r2.status_code == 200
