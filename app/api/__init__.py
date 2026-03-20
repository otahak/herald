"""Herald API routes."""

from app.api.games import GAMES_ROUTE_HANDLERS
from app.api.proxy import ProxyController
from app.api.websocket import websocket_handler
from app.api.feedback import FeedbackController
from app.api.admin import AdminController

__all__ = ["GAMES_ROUTE_HANDLERS", "ProxyController", "websocket_handler", "FeedbackController", "AdminController"]
