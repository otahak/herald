import logging
import os
from os import getenv
from pathlib import Path

# CRITICAL: Load .env file BEFORE any other imports that might use environment variables
# This must happen before importing routes, which imports OAuth module
ENV_FILE_PATHS = [
    Path("/opt/herald/.env"),
    Path(__file__).parent.parent / ".env",
    Path(__file__).parent.parent.parent / ".env",
]

def load_env_file_fallback():
    """Load .env file directly if environment variables aren't set."""
    # Use basic print for logging since logger might not be configured yet
    for env_file in ENV_FILE_PATHS:
        if env_file.exists() and env_file.is_file():
            try:
                loaded_count = 0
                with open(env_file, "r") as f:
                    for line in f:
                        line = line.strip()
                        # Skip empty lines and comments
                        if not line or line.startswith("#"):
                            continue
                        # Parse KEY=VALUE format
                        if "=" in line:
                            key, value = line.split("=", 1)
                            key = key.strip()
                            value = value.strip()
                            # Remove quotes if present
                            if value.startswith('"') and value.endswith('"'):
                                value = value[1:-1]
                            elif value.startswith("'") and value.endswith("'"):
                                value = value[1:-1]
                            # Only set if not already in environment
                            if key and value and key not in os.environ:
                                os.environ[key] = value
                                loaded_count += 1
                if loaded_count > 0:
                    print(f"[Herald] Loaded {loaded_count} environment variables from {env_file}")
                return True
            except Exception as e:
                print(f"[Herald] Warning: Could not load .env file from {env_file}: {e}")
    return False

# Load .env file if OAuth credentials aren't in environment
# This MUST happen before importing routes/oauth modules
if not getenv("GOOGLE_CLIENT_ID") or not getenv("GOOGLE_CLIENT_SECRET"):
    print("[Herald] OAuth credentials not in environment, loading from .env file...")
    load_env_file_fallback()
    # Verify they're now loaded
    if getenv("GOOGLE_CLIENT_ID") and getenv("GOOGLE_CLIENT_SECRET"):
        print("[Herald] ✓ OAuth credentials loaded from .env file")
    else:
        print("[Herald] ⚠ WARNING: OAuth credentials still not found after loading .env file")

from litestar import Litestar, Request
from litestar.contrib.sqlalchemy.plugins import SQLAlchemyInitPlugin, SQLAlchemyAsyncConfig
from litestar.contrib.jinja import JinjaTemplateEngine
from litestar.template.config import TemplateConfig
from typing import Any
from litestar.status_codes import HTTP_500_INTERNAL_SERVER_ERROR, HTTP_401_UNAUTHORIZED
from litestar.response import Response, Redirect
from litestar.exceptions import NotAuthorizedException

from app.routes import ROUTES
from app.models import Base  # Import models Base for table creation

DEBUG = getenv("APP_DEBUG", "false").lower() == "true"
# Default DATABASE_URL is for local dev only (Docker Compose)
# Production should always set DATABASE_URL environment variable
DATABASE_URL = getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@db:5432/herald"
)

logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("Herald")

logger.info(f"Starting app in {'DEBUG' if DEBUG else 'PRODUCTION'} mode")
logger.info(f"Database URL: {DATABASE_URL}")

# Check OAuth environment variables at startup
google_client_id = getenv("GOOGLE_CLIENT_ID")
google_client_secret = getenv("GOOGLE_CLIENT_SECRET")
if google_client_id and google_client_secret:
    logger.info("✓ OAuth environment variables are present in environment")
    logger.debug(f"GOOGLE_CLIENT_ID length: {len(google_client_id)}, GOOGLE_CLIENT_SECRET length: {len(google_client_secret)}")
else:
    logger.warning("⚠ OAuth environment variables NOT found in environment")
    logger.warning(f"GOOGLE_CLIENT_ID present: {bool(google_client_id)}, GOOGLE_CLIENT_SECRET present: {bool(google_client_secret)}")
    logger.warning("This usually means the .env file is not being loaded by systemd, or the .env file has formatting issues")

# --- SQLAlchemy config
config = SQLAlchemyAsyncConfig(
    connection_string=DATABASE_URL,
    session_dependency_key="session",
    metadata=Base.metadata,  # Use our models' metadata
    create_all=DEBUG,  # Auto-create tables on startup (dev only)
)
plugin = SQLAlchemyInitPlugin(config)

# --- Template config (auto-discovery)
template_dirs = [
    str(p) for p in Path(__file__).parent.glob("**/templates") if p.is_dir()
]

def register_template_globals(engine: JinjaTemplateEngine) -> None:
    """Register template globals and callables."""
    from app.utils import get_base_path
    
    def base_path_helper(ctx: dict[str, Any]) -> str:
        """Helper to get base path from request in templates."""
        # Request is automatically available in template context
        request = ctx.get("request")
        if request:
            try:
                return get_base_path(request)
            except Exception:
                return ""
        return ""
    
    # Register as a callable that templates can use
    engine.register_template_callable("get_base_path", base_path_helper)

template_config = TemplateConfig(
    directory=template_dirs,
    engine=JinjaTemplateEngine,
    engine_callback=register_template_globals,
)


# --- Exception handler
def log_exceptions(request: Request, exc: Exception) -> Response:
    logger.exception("Unhandled exception occurred", exc_info=exc)
    return Response(
        content={"detail": "Internal Server Error"},
        status_code=HTTP_500_INTERNAL_SERVER_ERROR,
        media_type="application/json"
    )


# --- Auth exception handler
def handle_auth_exception(request: Request, exc: NotAuthorizedException) -> Response:
    """Handle authentication failures by redirecting to login for pages, JSON for API."""
    from litestar.response import Redirect
    from app.utils import get_base_path
    path = request.url.path
    
    # API routes should return JSON, not redirect
    if "/api/" in path:
        return Response(
            content={"detail": "Not authorized", "error": str(exc)},
            status_code=HTTP_401_UNAUTHORIZED,
            media_type="application/json"
        )
    
    # For page routes, redirect to login
    if "/admin" in path or path.endswith("/admin"):
        # Extract base path if app is served under a subpath (e.g., /herald)
        base_path = get_base_path(request)
        return Redirect(f"{base_path}/admin/login-page")
    
    return Response(
        content={"detail": "Not authorized"},
        status_code=HTTP_401_UNAUTHORIZED,
        media_type="application/json"
    )

# --- App init
app = Litestar(
    route_handlers=ROUTES,
    debug=DEBUG,
    plugins=[plugin],
    template_config=template_config,
    exception_handlers={
        Exception: log_exceptions,
        NotAuthorizedException: handle_auth_exception,
    }
)
