"""Herald API routes."""

from app.api.games import GamesController
from app.api.proxy import ProxyController
from app.api.websocket import websocket_handler
from app.api.feedback import FeedbackController

__all__ = ["GamesController", "ProxyController", "websocket_handler", "FeedbackController"]
