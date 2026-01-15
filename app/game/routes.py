"""Game page routes."""

from litestar import get
from litestar.response import Template


@get("/game", sync_to_thread=False)
def game_lobby() -> Template:
    """Game lobby - create or join a game."""
    return Template(template_name="game/lobby.html")


@get("/game/{code:str}", sync_to_thread=False)
def game_board(code: str) -> Template:
    """Game board - play the game."""
    return Template(
        template_name="game/board.html",
        context={"game_code": code.upper()},
    )


routes = [game_lobby, game_board]
