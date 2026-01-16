from app.home.routes import routes as routes_home
from app.users.routes import routes as routes_users
from app.game.routes import routes as routes_game
from app.feedback.routes import routes as routes_feedback
from app.admin.routes import routes as routes_admin
from app.api import GamesController, ProxyController, FeedbackController, AdminController, websocket_handler
from litestar.static_files import create_static_files_router

ROUTES = [
    *routes_home,
    *routes_users,
    *routes_game,
    *routes_feedback,
    *routes_admin,
    GamesController,
    ProxyController,
    FeedbackController,
    AdminController,
    websocket_handler,
    create_static_files_router(
        path="/static",
        directories=["app/static"],
        name="static-files"
    )
]
