"""Game lifecycle API: create, get, join, start, patch state."""

import logging
import uuid
from datetime import datetime, timezone

from litestar import Controller, get, post, patch
from litestar.exceptions import ValidationException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.game_helpers import (
    broadcast_if_not_solo,
    check_and_update_expiration,
    get_game_by_code,
    log_event,
)
from app.api.game_schemas import (
    CreateGameRequest,
    GameResponse,
    GameWithUnitsResponse,
    JoinGameRequest,
    JoinGameResponse,
    UpdateGameStateRequest,
)
from app.api.games.common import unit_response_with_effective_caster
from app.models import (
    EventType,
    Game,
    GameEvent,
    GameStatus,
    GameSystem,
    Player,
)
from app.utils.logging import error_log

logger = logging.getLogger("Herald.games.lifecycle")


class GamesLifecycleController(Controller):
    """Create, read, join, start, and top-level game state."""

    path = "/api/games"
    tags = ["games", "games-lifecycle"]

    @post("/")
    async def create_game(
        self,
        data: CreateGameRequest,
        session: AsyncSession,
    ) -> GameResponse:
        """Create a new game and return the join code."""
        logger.info(f"Creating new game: '{data.name}' ({data.game_system})")
        
        try:
            # Create game (use default GFF if game_system not provided)
            game = Game(
                name=data.name,
                game_system=data.game_system or GameSystem.GFF,
                is_solo=data.is_solo,
            )
            session.add(game)
            await session.flush()  # Get game ID
            
            logger.debug(f"Game created with code: {game.code}")
            
            # Create host player
            player = Player(
                game_id=game.id,
                name=data.player_name,
                color=data.player_color,
                is_host=True,
            )
            session.add(player)
            await session.flush()
            
            # For solo mode, automatically create an opponent player
            if data.is_solo:
                opponent_display_name = (data.opponent_name or "Opponent").strip() or "Opponent"
                opponent = Player(
                    game_id=game.id,
                    name=opponent_display_name,
                    color="#ef4444",  # Red, different from default blue
                    is_host=False,
                )
                session.add(opponent)
                await session.flush()
            
            # Set current player
            game.current_player_id = player.id
            
            # Log event
            await log_event(
                session, game,
                EventType.GAME_STARTED,
                f"Game '{game.name}' created by {player.name}",
                player_id=player.id,
            )
            
            await session.commit()
            await session.refresh(game)
            
            # Reload with relationships
            game = await get_game_by_code(session, game.code)
            logger.info(f"Game created successfully: {game.code} (host: {player.name})")
            return GameResponse.model_validate(game)
        except Exception as e:
            error_log(
                "Failed to create game",
                exc=e,
                context={
                    "game_name": data.name,
                    "game_system": str(data.game_system) if data.game_system else "GFF",
                    "player_name": data.player_name,
                }
            )
            raise
    
    @get("/{code:str}")
    async def get_game(
        self,
        code: str,
        session: AsyncSession,
    ) -> GameWithUnitsResponse:
        """Get game state by join code."""
        game = await get_game_by_code(session, code)
        
        # Check and update expiration status
        check_and_update_expiration(game)
        if game.status == GameStatus.EXPIRED:
            await session.commit()  # pragma: no cover
        
        # Collect all units from all players
        units = []
        for player in game.players:
            units.extend(player.units)
        
        response = GameWithUnitsResponse.model_validate(game)
        response.units = [unit_response_with_effective_caster(u) for u in units]
        return response
    
    @post("/{code:str}/join")
    async def join_game(
        self,
        code: str,
        data: JoinGameRequest,
        session: AsyncSession,
    ) -> JoinGameResponse:
        """Join an existing game as a new player."""
        game = await get_game_by_code(session, code)
        
        if game.status != GameStatus.LOBBY:
            raise ValidationException("Cannot join a game that has already started")
        
        if len(game.players) >= 2:
            raise ValidationException("Game is full")
        
        # Create player
        player = Player(
            game_id=game.id,
            name=data.player_name,
            color=data.player_color,
            is_host=False,
            is_connected=False,  # mark disconnected until WebSocket joins
        )
        session.add(player)
        await session.flush()  # Get player ID
        
        # Store values before commit (to avoid lazy load after commit)
        player_id = player.id
        player_name = player.name
        player_color = player.color
        game_id = game.id
        current_round = game.current_round
        
        # Log event - create directly to avoid relationship access
        event = GameEvent(
            game_id=game_id,
            player_id=player_id,
            event_type=EventType.PLAYER_JOINED,
            description=f"{player_name} joined the game",
            round_number=current_round,
        )
        session.add(event)
        
        await session.commit()
        
        # Reload game to get is_solo flag
        game = await get_game_by_code(session, code)
        
        # Broadcast to WebSocket clients (notify host) - skip for solo games
        await broadcast_if_not_solo(game, code, {
            "type": "player_joined",
            "player": {
                "id": str(player_id),
                "name": player_name,
                "color": player_color,
                "is_host": False,
                "is_connected": False,
            }
        })
        logger.info(f"Player {player_name} joined game {code}, broadcast sent")
        
        # Reload game
        game = await get_game_by_code(session, code)
        units = []
        for p in game.players:
            units.extend(p.units)
        
        response = JoinGameResponse.model_validate(game)
        response.units = [unit_response_with_effective_caster(u) for u in units]
        response.your_player_id = str(player_id)  # Tell client which player they are
        return response
    
    @post("/{code:str}/start")
    async def start_game(
        self,
        code: str,
        session: AsyncSession,
    ) -> GameWithUnitsResponse:
        """Start the game (transition from lobby to in_progress)."""
        game = await get_game_by_code(session, code)
        
        # Update activity tracking
        game.last_activity_at = datetime.now(timezone.utc)
        
        if game.status != GameStatus.LOBBY:
            raise ValidationException("Game has already started")
        
        # Solo mode can start with 1 player, multiplayer needs 2
        if not game.is_solo:
            if len(game.players) < 2:
                raise ValidationException("Need at least 2 players to start")
            
            # Check both players have units (multiplayer only)
            for player in game.players:
                if not player.units:
                    raise ValidationException(f"Player {player.name} has no units")
        else:
            # Solo mode: check at least one player has units
            if len(game.players) == 0:
                raise ValidationException("Need at least 1 player to start")
            has_units = any(player.units for player in game.players)
            if not has_units:
                raise ValidationException("Need at least one player with units to start")
        
        # Start the game
        game.status = GameStatus.IN_PROGRESS
        game.current_round = 1
        
        # Set starting counts for morale tracking
        for player in game.players:
            player.starting_unit_count = len(player.units)
            player.starting_points = sum(u.cost for u in player.units)
        
        # Log event
        await log_event(
            session, game,
            EventType.GAME_STARTED,
            f"Game started! Round 1 begins.",
        )
        
        await session.commit()
        
        # Reload game to get is_solo flag
        game = await get_game_by_code(session, code)
        
        # Broadcast to WebSocket clients - skip for solo games
        await broadcast_if_not_solo(game, code, {
            "type": "game_started",
            "status": "in_progress",
            "current_round": 1,
        })
        logger.info(f"Game {code} started, broadcast sent")
        
        # Reload game
        game = await get_game_by_code(session, code)
        units = []
        for p in game.players:
            units.extend(p.units)
        
        response = GameWithUnitsResponse.model_validate(game)
        response.units = [unit_response_with_effective_caster(u) for u in units]
        return response
    
    @patch("/{code:str}/state")
    async def update_game_state(
        self,
        code: str,
        data: UpdateGameStateRequest,
        session: AsyncSession,
    ) -> GameResponse:
        """Update game state (round, turn, status)."""
        game = await get_game_by_code(session, code)
        
        # Update activity tracking
        game.last_activity_at = datetime.now(timezone.utc)
        
        if data.current_round is not None:
            old_round = game.current_round
            game.current_round = data.current_round
            
            if data.current_round > old_round:
                # New round - reset activations
                for player in game.players:
                    player.has_finished_activations = False
                    for unit in player.units:
                        if unit.state:
                            unit.state.reset_for_new_round()
                
                await log_event(
                    session, game,
                    EventType.ROUND_STARTED,
                    f"Round {data.current_round} started",
                )
        
        if data.status is not None:
            game.status = data.status
            if data.status == GameStatus.COMPLETED:
                await log_event(
                    session, game,
                    EventType.GAME_ENDED,
                    "Game ended",
                )
        
        if data.current_player_id is not None:
            game.current_player_id = data.current_player_id
        
        await session.commit()
        await session.refresh(game)
        
        # Broadcast state update to other clients - skip for solo games
        await broadcast_if_not_solo(game, code, {
            "type": "state_update",
            "data": {
                "current_round": game.current_round,
                "status": game.status.value,
            }
        })
        
        return GameResponse.model_validate(game)
