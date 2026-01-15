from litestar import get
from litestar.response import Template

# Serve game lobby at root
@get("/", sync_to_thread=False)
def home() -> Template:
    """Game lobby - create or join a game (served at root)."""
    return Template(template_name="game/lobby.html")

routes = [home]