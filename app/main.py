import logging
from os import getenv
from pathlib import Path

from litestar import Litestar, Request
from litestar.contrib.sqlalchemy.plugins import SQLAlchemyInitPlugin, SQLAlchemyAsyncConfig
from litestar.contrib.jinja import JinjaTemplateEngine
from litestar.template.config import TemplateConfig
from litestar.status_codes import HTTP_500_INTERNAL_SERVER_ERROR
from litestar.response import Response

from app.routes import ROUTES
from app.models import Base  # Import models Base for table creation

DEBUG = getenv("APP_DEBUG", "false").lower() == "true"
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

# --- SQLAlchemy config
config = SQLAlchemyAsyncConfig(
    connection_string=DATABASE_URL,
    session_dependency_key="session",
    metadata=Base.metadata,  # Use our models' metadata
    create_all=True,  # Auto-create tables on startup (dev only)
)
plugin = SQLAlchemyInitPlugin(config)

# --- Template config (auto-discovery)
template_dirs = [
    str(p) for p in Path(__file__).parent.glob("**/templates") if p.is_dir()
]
template_config = TemplateConfig(
    directory=template_dirs,
    engine=JinjaTemplateEngine
)


# --- Exception handler
def log_exceptions(request: Request, exc: Exception) -> Response:
    logger.exception("Unhandled exception occurred", exc_info=exc)
    return Response(
        content={"detail": "Internal Server Error"},
        status_code=HTTP_500_INTERNAL_SERVER_ERROR,
        media_type="application/json"
    )

# --- App init
app = Litestar(
    route_handlers=ROUTES,
    debug=DEBUG,
    plugins=[plugin],
    template_config=template_config,
    exception_handlers={Exception: log_exceptions}
)
