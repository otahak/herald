"""Hit admin OAuth redirect routes via sync TestClient (admin/routes.py)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from litestar.response import Redirect


@pytest.fixture
def admin_session_store():
    import app.auth.oauth as oauth

    async def store_get(key):
        if oauth.ADMIN_SESSION_KEY in key:
            return "true"
        if oauth.ADMIN_EMAIL_KEY in key:
            return oauth.GOOGLE_AUTHORIZED_EMAIL
        return None

    st = MagicMock()
    st.get = AsyncMock(side_effect=store_get)
    return oauth, st


def test_admin_login_callback_logout_routes_delegate_to_oauth(sync_client, admin_session_store):
    oauth, _st = admin_session_store
    redir = Redirect(path="/x")

    with patch.object(oauth, "session_store", _st):
        import app.admin.routes as admin_routes

        with patch.object(admin_routes, "admin_login", new=AsyncMock(return_value=redir)) as m_login:
            r = sync_client.get("/admin/login", follow_redirects=False)
            assert r.status_code in (301, 302, 303, 307, 308)
            m_login.assert_awaited_once()

        with patch.object(admin_routes, "admin_callback", new=AsyncMock(return_value=redir)) as m_cb:
            r2 = sync_client.get("/admin/callback", follow_redirects=False)
            assert r2.status_code in (301, 302, 303, 307, 308)
            m_cb.assert_awaited_once()

        with patch.object(admin_routes, "admin_logout", new=AsyncMock(return_value=redir)) as m_out:
            r3 = sync_client.get("/admin/logout", follow_redirects=False)
            assert r3.status_code in (301, 302, 303, 307, 308)
            m_out.assert_awaited_once()
