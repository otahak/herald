"""Game page routes."""

from litestar import get, Request
from litestar.response import Redirect, Template

from app.auth.oauth import is_admin_authenticated
from app.utils import get_base_path


@get("/game", sync_to_thread=False)
def game_lobby() -> Template:
    """Game lobby - create or join a game."""
    return Template(template_name="game/lobby.html")


@get("/game/{code:str}")
async def game_board(request: Request, code: str) -> Redirect | Template:
    """Game board - play the game. Admins are redirected to observer mode so they don't use a player slot."""
    if await is_admin_authenticated(request):
        base_path = get_base_path(request)
        return Redirect(f"{base_path}/admin/observe/{code.upper()}")
    return Template(
        template_name="game/board.html",
        context={"game_code": code.upper(), "observer_mode": False},
    )


routes = [game_lobby, game_board]
