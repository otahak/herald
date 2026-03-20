"""Game HTTP API (split across multiple controllers on ``/api/games``)."""

from app.api.games.events import GamesEventsController
from app.api.games.lifecycle import GamesLifecycleController
from app.api.games.meta import GamesMetaController
from app.api.games.objectives import GamesObjectivesController
from app.api.games.saves import GamesSavesController
from app.api.games.units_combat import GamesUnitsCombatController
from app.api.games.units_state import GamesUnitsStateController

GAMES_ROUTE_HANDLERS = [
    GamesLifecycleController,
    GamesUnitsStateController,
    GamesUnitsCombatController,
    GamesObjectivesController,
    GamesEventsController,
    GamesMetaController,
    GamesSavesController,
]

__all__ = [
    "GAMES_ROUTE_HANDLERS",
    "GamesLifecycleController",
    "GamesUnitsStateController",
    "GamesUnitsCombatController",
    "GamesObjectivesController",
    "GamesEventsController",
    "GamesMetaController",
    "GamesSavesController",
]
