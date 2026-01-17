"""Admin page routes."""

from litestar import get, Request
from litestar.response import Redirect, Template

from app.auth.oauth import require_admin_guard, admin_login, admin_callback, admin_logout


@get("/admin", guards=[require_admin_guard])
async def admin_dashboard(request: Request) -> Template:
    """Admin dashboard page (requires authentication)."""
    from app.auth.oauth import get_base_path
    base_path = get_base_path(request)
    return Template(template_name="admin/dashboard.html", context={"base_path": base_path})


@get("/admin/login-page")
async def admin_login_page(request: Request) -> Template:
    """Admin login page (shown when not authenticated)."""
    from app.auth.oauth import get_base_path
    base_path = get_base_path(request)
    return Template(template_name="admin/login.html", context={"base_path": base_path})


@get("/admin/login")
async def admin_login_route(request: Request) -> Redirect:
    """Redirect to Google OAuth login."""
    return await admin_login(request)


@get("/admin/callback")
async def admin_callback_route(request: Request) -> Redirect:
    """Handle OAuth callback."""
    return await admin_callback(request)


@get("/admin/logout")
async def admin_logout_get(request: Request) -> Redirect:
    """Log out admin user (GET handler)."""
    return await admin_logout(request)


routes = [admin_dashboard, admin_login_route, admin_callback_route, admin_logout_get, admin_login_page]
