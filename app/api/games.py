"""Game session API endpoints."""

import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Any

from litestar import Controller, get, post, patch, delete, Request
from litestar.response import Response
from litestar.dto import DTOConfig
from litestar.exceptions import NotFoundException, ValidationException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    Game, GameSystem, GameStatus,
    Player, Unit, UnitState, DeploymentStatus,
    Objective, ObjectiveStatus,
    GameEvent, EventType,
    GameSave,
)
from app.api.websocket import broadcast_to_game
from app.utils.logging import error_log, log_exception_with_context

# Import parse_special_rules from proxy module for rule parsing
from app.api.proxy import parse_special_rules

logger = logging.getLogger("Herald.games")


# --- Request/Response Schemas ---

class CreateGameRequest(BaseModel):
    """Request to create a new game."""
    name: str = Field(default="New Game", max_length=100)
    game_system: Optional[GameSystem] = Field(default=None)
    player_name: str = Field(max_length=50)
    player_color: str = Field(default="#3b82f6", max_length=20)
    is_solo: bool = Field(default=False, description="Enable solo play mode (single player controls both armies)")


class JoinGameRequest(BaseModel):
    """Request to join an existing game."""
    player_name: str = Field(max_length=50)
    player_color: str = Field(default="#ef4444", max_length=20)  # Red for player 2


class UpdateGameStateRequest(BaseModel):
    """Request to update game state."""
    current_round: Optional[int] = None
    status: Optional[GameStatus] = None
    current_player_id: Optional[uuid.UUID] = None


class UpdateUnitStateRequest(BaseModel):
    """Request to update a unit's state."""
    wounds_taken: Optional[int] = None
    models_remaining: Optional[int] = None
    activated_this_round: Optional[bool] = None
    is_shaken: Optional[bool] = None
    is_fatigued: Optional[bool] = None
    deployment_status: Optional[DeploymentStatus] = None
    transport_id: Optional[uuid.UUID] = None
    spell_tokens: Optional[int] = None
    limited_weapons_used: Optional[List[str]] = None
    custom_notes: Optional[str] = None


class UpdateObjectiveRequest(BaseModel):
    """Request to update an objective's state."""
    status: ObjectiveStatus
    controlled_by_id: Optional[uuid.UUID] = None


class CreateObjectivesRequest(BaseModel):
    """Request to create objectives for a game."""
    count: int = Field(ge=3, le=6, default=4)


class UpdateVictoryPointsRequest(BaseModel):
    """Request to update a player's victory points."""
    delta: int = Field(description="Change in VP (+1, -1, etc.)")


class UpdateRoundRequest(BaseModel):
    """Request to update the game round."""
    delta: int = Field(description="Change in round (+1, -1, etc.)")


class CreateUnitRequest(BaseModel):
    """Request to create a unit manually."""
    player_id: uuid.UUID = Field(..., description="Player ID who owns this unit")
    name: str = Field(..., min_length=1, max_length=100, description="Unit name")
    custom_name: Optional[str] = Field(None, max_length=100, description="Custom display name")
    quality: int = Field(4, ge=2, le=6, description="Quality stat (2-6)")
    defense: int = Field(4, ge=2, le=6, description="Defense stat (2-6)")
    size: int = Field(1, ge=1, description="Number of models")
    tough: int = Field(1, ge=1, description="Tough value")
    cost: int = Field(0, ge=0, description="Point cost")
    loadout: Optional[List[Any]] = Field(None, description="Weapon loadout (JSON)")
    rules: Optional[List[Any]] = Field(None, description="Special rules (JSON)")
    is_hero: bool = Field(False, description="Is this a hero unit")
    is_caster: bool = Field(False, description="Is this a caster unit")
    caster_level: int = Field(0, ge=0, le=6, description="Caster level (0-6)")
    is_transport: bool = Field(False, description="Is this a transport unit")
    transport_capacity: int = Field(0, ge=0, description="Transport capacity")
    has_ambush: bool = Field(False, description="Has Ambush rule")
    has_scout: bool = Field(False, description="Has Scout rule")
    attached_to_unit_id: Optional[uuid.UUID] = Field(None, description="Attach to parent unit (for heroes)")


class ClearUnitsResponse(BaseModel):
    """Response after clearing all units."""
    success: bool
    units_cleared: int
    message: str


class SaveGameRequest(BaseModel):
    """Request to save a game state."""
    save_name: str = Field(default="Untitled Save", max_length=100)
    description: Optional[str] = Field(None, max_length=500)


class SaveGameResponse(BaseModel):
    """Response after saving a game."""
    success: bool
    save_id: uuid.UUID
    save_name: str
    message: str


class GameSaveResponse(BaseModel):
    """Response for a game save."""
    id: uuid.UUID
    game_id: uuid.UUID
    save_name: str
    saved_at: datetime
    description: Optional[str]
    
    class Config:
        from_attributes = True


class LoadGameRequest(BaseModel):
    """Request to load a game state."""
    save_id: uuid.UUID


# --- Response Schemas ---

class PlayerResponse(BaseModel):
    """Player data response."""
    id: uuid.UUID
    name: str
    color: str
    is_host: bool
    is_connected: bool
    army_name: Optional[str]
    starting_unit_count: int
    starting_points: int
    victory_points: int
    
    class Config:
        from_attributes = True


class UnitStateResponse(BaseModel):
    """Unit state response."""
    id: uuid.UUID
    wounds_taken: int
    models_remaining: int
    activated_this_round: bool
    is_shaken: bool
    is_fatigued: bool
    deployment_status: DeploymentStatus
    transport_id: Optional[uuid.UUID]
    spell_tokens: int
    limited_weapons_used: Optional[List[str]]
    custom_notes: Optional[str]
    
    class Config:
        from_attributes = True


class UnitResponse(BaseModel):
    """Unit data response."""
    id: uuid.UUID
    player_id: uuid.UUID
    name: str
    custom_name: Optional[str]
    quality: int
    defense: int
    size: int
    tough: int
    cost: int
    loadout: Optional[List[Any]]
    rules: Optional[List[Any]]
    is_hero: bool
    is_caster: bool
    caster_level: int
    is_transport: bool
    transport_capacity: int
    has_ambush: bool
    has_scout: bool
    attached_to_unit_id: Optional[uuid.UUID] = None
    state: Optional[UnitStateResponse]
    
    class Config:
        from_attributes = True


class ObjectiveResponse(BaseModel):
    """Objective data response."""
    id: uuid.UUID
    marker_number: int
    label: Optional[str]
    status: ObjectiveStatus
    controlled_by_id: Optional[uuid.UUID]
    
    class Config:
        from_attributes = True


class GameEventResponse(BaseModel):
    """Game event response."""
    id: uuid.UUID
    event_type: EventType
    description: str
    round_number: int
    player_id: Optional[uuid.UUID]
    target_unit_id: Optional[uuid.UUID]
    target_objective_id: Optional[uuid.UUID]
    details: Optional[dict]
    is_undone: bool
    created_at: datetime
    
    class Config:
        from_attributes = True


class GameResponse(BaseModel):
    """Full game state response."""
    id: uuid.UUID
    code: str
    name: str
    game_system: GameSystem
    status: GameStatus
    is_solo: bool
    current_round: int
    max_rounds: int
    current_player_id: Optional[uuid.UUID]
    first_player_next_round_id: Optional[uuid.UUID]
    players: List[PlayerResponse]
    objectives: List[ObjectiveResponse]
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


class GameWithUnitsResponse(GameResponse):
    """Game response with full unit data."""
    units: List[UnitResponse] = []


class JoinGameResponse(GameWithUnitsResponse):
    """Response when joining a game - includes your player ID."""
    your_player_id: str = ""  # The ID of the player who just joined (set after validation)


# --- Helper Functions ---

async def broadcast_if_not_solo(game: Game, code: str, message: dict) -> None:
    """Broadcast to game only if not in solo mode."""
    if not game.is_solo:
        await broadcast_to_game(code, message)


async def get_game_by_code(session: AsyncSession, code: str, load_attached_heroes: bool = False) -> Game:
    """Fetch game by join code with relationships loaded."""
    stmt = (
        select(Game)
        .where(Game.code == code.upper())
        .options(
            selectinload(Game.players).selectinload(Player.units).selectinload(Unit.state),
            selectinload(Game.objectives),
        )
    )
    # Only eagerly load attached_heroes if explicitly requested
    # This allows the code to work even if migrations haven't run yet
    if load_attached_heroes:
        stmt = stmt.options(
            selectinload(Game.players).selectinload(Player.units).selectinload(Unit.attached_heroes)
        )
    
    result = await session.execute(stmt)
    game = result.scalar_one_or_none()
    if not game:
        raise NotFoundException(f"Game with code '{code}' not found")
    
    return game


def check_and_update_expiration(game: Game) -> bool:
    """
    Check if a game has expired based on its type and activity.
    
    Returns:
        True if game is expired, False otherwise
    
    Expiration rules:
        - Multiplayer games: Expire after 1 hour of no connected users
        - Solo games: Expire after 30 days of no activity
    """
    if game.status == GameStatus.EXPIRED:
        return True
    
    now = datetime.now(timezone.utc)
    
    # If no activity tracking, game hasn't expired yet
    if not game.last_activity_at:
        return False
    
    # Check expiration based on game type
    if game.is_solo:
        # Solo games expire after 30 days of inactivity
        expiration_threshold = timedelta(days=30)
        if now - game.last_activity_at > expiration_threshold:
            game.status = GameStatus.EXPIRED
            return True
    else:
        # Multiplayer games expire after 1 hour of no connected users
        # First check if all players are disconnected
        all_disconnected = all(not p.is_connected for p in game.players) if game.players else True
        
        if all_disconnected:
            expiration_threshold = timedelta(hours=1)
            if now - game.last_activity_at > expiration_threshold:
                game.status = GameStatus.EXPIRED
                return True
    
    return False


async def log_event(
    session: AsyncSession,
    game: Game,
    event_type: EventType,
    description: str,
    player_id: Optional[uuid.UUID] = None,
    target_unit_id: Optional[uuid.UUID] = None,
    target_objective_id: Optional[uuid.UUID] = None,
    details: Optional[dict] = None,
    previous_state: Optional[dict] = None,
) -> GameEvent:
    """Create and persist a game event."""
    event = GameEvent.create(
        game_id=game.id,
        event_type=event_type,
        description=description,
        player_id=player_id,
        round_number=game.current_round,
        target_unit_id=target_unit_id,
        target_objective_id=target_objective_id,
        details=details,
        previous_state=previous_state,
    )
    session.add(event)
    return event


# --- Controller ---

class GamesController(Controller):
    """API endpoints for game management."""
    
    path = "/api/games"
    tags = ["games"]
    
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
                opponent = Player(
                    game_id=game.id,
                    name="Opponent",
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
            await session.commit()
        
        # Collect all units from all players
        units = []
        for player in game.players:
            units.extend(player.units)
        
        response = GameWithUnitsResponse.model_validate(game)
        response.units = [UnitResponse.model_validate(u) for u in units]
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
        response.units = [UnitResponse.model_validate(u) for u in units]
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
        response.units = [UnitResponse.model_validate(u) for u in units]
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
    
    @patch("/{code:str}/units/{unit_id:uuid}")
    async def update_unit_state(
        self,
        code: str,
        unit_id: uuid.UUID,
        data: UpdateUnitStateRequest,
        session: AsyncSession,
    ) -> UnitResponse:
        """Update a unit's game state."""
        game = await get_game_by_code(session, code, load_attached_heroes=True)
        
        # Update activity tracking
        game.last_activity_at = datetime.now(timezone.utc)
        
        # Find the unit
        unit = None
        for player in game.players:
            for u in player.units:
                if u.id == unit_id:
                    unit = u
                    break
        
        if not unit:
            raise NotFoundException(f"Unit {unit_id} not found in game")
        
        if not unit.state:
            raise ValidationException("Unit has no state (not initialized)")
        
        # Track changes for logging
        changes = []
        previous_state = {}
        
        if data.wounds_taken is not None and data.wounds_taken != unit.state.wounds_taken:
            previous_state["wounds_taken"] = unit.state.wounds_taken
            wound_diff = data.wounds_taken - unit.state.wounds_taken
            unit.state.wounds_taken = data.wounds_taken
            
            if wound_diff > 0:
                # Adding wounds: Create one log entry for each wound (like VP)
                changes.append(f"took {wound_diff} wound(s)")
                for i in range(wound_diff):
                    wounds_at_this_point = previous_state["wounds_taken"] + i
                    await log_event(
                        session, game,
                        EventType.UNIT_WOUNDED,
                        f"{unit.display_name} took 1 wound ({unit.max_wounds - wounds_at_this_point - 1}/{unit.max_wounds} remaining)",
                        player_id=unit.player_id,
                        target_unit_id=unit.id,
                        details={
                            "wounds": 1,
                            "wounds_before": wounds_at_this_point,
                            "wounds_after": wounds_at_this_point + 1,
                            "timestamp": datetime.utcnow().isoformat(),
                        },
                        previous_state={"wounds_taken": wounds_at_this_point},
                    )
            else:
                # Removing wounds: Check if recent wound events should be deleted or logged as heals
                wounds_to_remove = abs(wound_diff)
                changes.append(f"removed {wounds_to_remove} wound(s)")
                
                # Find the most recent UNIT_WOUNDED events for this unit
                stmt = (
                    select(GameEvent)
                    .where(GameEvent.game_id == game.id)
                    .where(GameEvent.event_type == EventType.UNIT_WOUNDED)
                    .where(GameEvent.target_unit_id == unit_id)
                    .where(GameEvent.is_undone == False)
                    .order_by(GameEvent.created_at.desc())
                    .limit(wounds_to_remove)
                )
                result = await session.execute(stmt)
                recent_wound_events = result.scalars().all()
                
                # Use timezone-aware datetime for comparison (created_at is timezone-aware from DB)
                current_time = datetime.now(timezone.utc)
                threshold_time = current_time - timedelta(seconds=30)
                
                for event in recent_wound_events:
                    # Check if event was created within the last 30 seconds
                    # created_at is timezone-aware, compare directly
                    if event.created_at >= threshold_time:
                        # Delete the event (wound was removed quickly, likely a mistake)
                        await session.delete(event)
                    else:
                        # Event is older than 30 seconds, log as a heal
                        await log_event(
                            session, game,
                            EventType.UNIT_HEALED,
                            f"{unit.display_name} healed 1 wound",
                            player_id=unit.player_id,
                            target_unit_id=unit.id,
                            details={"wounds_healed": 1},
                        )
        
        if data.models_remaining is not None:
            unit.state.models_remaining = data.models_remaining
        
        if data.activated_this_round is not None and data.activated_this_round != unit.state.activated_this_round:
            # Prevent activating attached heroes separately
            if data.activated_this_round and unit.attached_to_unit_id:
                raise ValidationException(
                    f"{unit.display_name} is attached to another unit and cannot be activated separately. "
                    f"Activate the parent unit instead."
                )
            
            unit.state.activated_this_round = data.activated_this_round
            if data.activated_this_round:
                await log_event(
                    session, game,
                    EventType.UNIT_ACTIVATED,
                    f"{unit.display_name} activated",
                    player_id=unit.player_id,
                    target_unit_id=unit.id,
                )
                
                # When activating a unit, also activate any attached heroes
                if unit.attached_heroes:
                    for attached_hero in unit.attached_heroes:
                        if attached_hero.state and not attached_hero.state.activated_this_round:
                            attached_hero.state.activated_this_round = True
                            await log_event(
                                session, game,
                                EventType.UNIT_ACTIVATED,
                                f"{attached_hero.display_name} activated (attached to {unit.display_name})",
                                player_id=attached_hero.player_id,
                                target_unit_id=attached_hero.id,
                            )
        
        if data.is_shaken is not None and data.is_shaken != unit.state.is_shaken:
            unit.state.is_shaken = data.is_shaken
            if data.is_shaken:
                await log_event(
                    session, game,
                    EventType.STATUS_SHAKEN,
                    f"{unit.display_name} became Shaken",
                    player_id=unit.player_id,
                    target_unit_id=unit.id,
                )
            else:
                await log_event(
                    session, game,
                    EventType.STATUS_SHAKEN_CLEARED,
                    f"{unit.display_name} is no longer Shaken",
                    player_id=unit.player_id,
                    target_unit_id=unit.id,
                )
            
            # Sync shaken status to attached heroes (they share status with parent)
            if unit.attached_heroes:
                for attached_hero in unit.attached_heroes:
                    if attached_hero.state and attached_hero.state.is_shaken != data.is_shaken:
                        attached_hero.state.is_shaken = data.is_shaken
                        if data.is_shaken:
                            await log_event(
                                session, game,
                                EventType.STATUS_SHAKEN,
                                f"{attached_hero.display_name} became Shaken (attached to {unit.display_name})",
                                player_id=attached_hero.player_id,
                                target_unit_id=attached_hero.id,
                            )
                        else:
                            await log_event(
                                session, game,
                                EventType.STATUS_SHAKEN_CLEARED,
                                f"{attached_hero.display_name} is no longer Shaken (attached to {unit.display_name})",
                                player_id=attached_hero.player_id,
                                target_unit_id=attached_hero.id,
                            )
        
        if data.is_fatigued is not None:
            unit.state.is_fatigued = data.is_fatigued
            if data.is_fatigued:
                await log_event(
                    session, game,
                    EventType.STATUS_FATIGUED,
                    f"{unit.display_name} became Fatigued",
                    target_unit_id=unit.id,
                )
        
        if data.deployment_status is not None and data.deployment_status != unit.state.deployment_status:
            old_status = unit.state.deployment_status
            unit.state.deployment_status = data.deployment_status
            
            if data.deployment_status == DeploymentStatus.DEPLOYED and old_status == DeploymentStatus.IN_AMBUSH:
                await log_event(
                    session, game,
                    EventType.UNIT_DEPLOYED,
                    f"{unit.display_name} deployed from Ambush",
                    target_unit_id=unit.id,
                )
            elif data.deployment_status == DeploymentStatus.DESTROYED:
                await log_event(
                    session, game,
                    EventType.UNIT_DESTROYED,
                    f"{unit.display_name} was destroyed",
                    target_unit_id=unit.id,
                )
                
                # Automatically detach any attached heroes when parent is destroyed
                # Heroes may survive as independent units
                # If parent was shaken, preserve that status on the detached hero
                parent_was_shaken = unit.state.is_shaken
                if unit.attached_heroes:
                    for attached_hero in unit.attached_heroes:
                        # Preserve shaken status if parent was shaken
                        if parent_was_shaken and attached_hero.state:
                            if not attached_hero.state.is_shaken:
                                attached_hero.state.is_shaken = True
                                await log_event(
                                    session, game,
                                    EventType.STATUS_SHAKEN,
                                    f"{attached_hero.display_name} remains Shaken after detachment (parent was Shaken)",
                                    player_id=attached_hero.player_id,
                                    target_unit_id=attached_hero.id,
                                )
                        
                        attached_hero.attached_to_unit_id = None
                        await log_event(
                            session, game,
                            EventType.UNIT_DETACHED,
                            f"{attached_hero.display_name} detached from {unit.display_name} (parent destroyed)",
                            player_id=attached_hero.player_id,
                            target_unit_id=attached_hero.id,
                        )
        
        if data.transport_id is not None:
            old_transport = unit.state.transport_id
            unit.state.transport_id = data.transport_id
            unit.state.deployment_status = DeploymentStatus.EMBARKED
            await log_event(
                session, game,
                EventType.UNIT_EMBARKED,
                f"{unit.display_name} embarked on transport",
                target_unit_id=unit.id,
            )
        elif data.transport_id is None and unit.state.transport_id is not None:
            unit.state.transport_id = None
            unit.state.deployment_status = DeploymentStatus.DEPLOYED
            await log_event(
                session, game,
                EventType.UNIT_DISEMBARKED,
                f"{unit.display_name} disembarked from transport",
                target_unit_id=unit.id,
            )
        
        if data.spell_tokens is not None and data.spell_tokens != unit.state.spell_tokens:
            old_tokens = unit.state.spell_tokens
            unit.state.spell_tokens = min(6, max(0, data.spell_tokens))  # Clamp 0-6
            
            diff = unit.state.spell_tokens - old_tokens
            if diff > 0:
                await log_event(
                    session, game,
                    EventType.SPELL_TOKENS_GAINED,
                    f"{unit.display_name} gained {diff} spell token(s) ({unit.state.spell_tokens}/6)",
                    target_unit_id=unit.id,
                    details={"tokens_gained": diff, "tokens_total": unit.state.spell_tokens},
                )
            elif diff < 0:
                await log_event(
                    session, game,
                    EventType.SPELL_TOKENS_SPENT,
                    f"{unit.display_name} spent {-diff} spell token(s) ({unit.state.spell_tokens}/6)",
                    target_unit_id=unit.id,
                    details={"tokens_spent": -diff, "tokens_total": unit.state.spell_tokens},
                )
        
        if data.limited_weapons_used is not None:
            old_weapons = unit.state.limited_weapons_used or []
            unit.state.limited_weapons_used = data.limited_weapons_used
            
            # Log newly used weapons
            new_weapons = set(data.limited_weapons_used) - set(old_weapons)
            for weapon in new_weapons:
                await log_event(
                    session, game,
                    EventType.LIMITED_WEAPON_USED,
                    f"{unit.display_name} used {weapon} (Limited)",
                    target_unit_id=unit.id,
                    details={"weapon_name": weapon},
                )
        
        if data.custom_notes is not None:
            unit.state.custom_notes = data.custom_notes
        
        await session.commit()
        await session.refresh(unit)
        
        # Reload game to get is_solo flag
        game = await get_game_by_code(session, code)
        
        # Broadcast state update to trigger event fetching on other clients - skip for solo games
        await broadcast_if_not_solo(game, code, {
            "type": "state_update",
            "data": {
                "reason": "unit_updated",
                "unit_id": str(unit_id),
            }
        })
        
        return UnitResponse.model_validate(unit)
    
    @post("/{code:str}/units/manual")
    async def create_unit_manually(
        self,
        code: str,
        data: CreateUnitRequest,
        session: AsyncSession,
    ) -> UnitResponse:
        """Create a unit manually (alternative to Army Forge import)."""
        game = await get_game_by_code(session, code, load_attached_heroes=True)
        
        # Only allow in lobby status
        if game.status != GameStatus.LOBBY:
            raise ValidationException("Units can only be added manually in the lobby")
        
        # Find the player
        player = None
        for p in game.players:
            if p.id == data.player_id:
                player = p
                break
        
        if not player:
            raise NotFoundException(f"Player {data.player_id} not found in game")
        
        # Cache player.id immediately after finding player to avoid accessing after commit
        player_id = player.id
        
        # If rules are provided, parse them to potentially override flags
        # But allow direct flag setting to take precedence
        props = {
            "is_hero": data.is_hero,
            "is_caster": data.is_caster,
            "caster_level": data.caster_level if data.is_caster else 0,
            "is_transport": data.is_transport,
            "transport_capacity": data.transport_capacity if data.is_transport else 0,
            "has_ambush": data.has_ambush,
            "has_scout": data.has_scout,
            "tough": data.tough,
        }
        
        # If rules are provided, parse them but let direct flags override
        if data.rules:
            parsed_props = parse_special_rules(data.rules)
            # Only use parsed values if flags weren't explicitly set
            if not data.is_hero:
                props["is_hero"] = parsed_props["is_hero"]
            if not data.is_caster:
                props["is_caster"] = parsed_props["is_caster"]
                props["caster_level"] = parsed_props["caster_level"]
            if not data.is_transport:
                props["is_transport"] = parsed_props["is_transport"]
                props["transport_capacity"] = parsed_props["transport_capacity"]
            if not data.has_ambush:
                props["has_ambush"] = parsed_props["has_ambush"]
            if not data.has_scout:
                props["has_scout"] = parsed_props["has_scout"]
        
        # Validate attachment if provided
        if data.attached_to_unit_id:
            parent_unit = None
            for p in game.players:
                for u in p.units:
                    if u.id == data.attached_to_unit_id:
                        parent_unit = u
                        break
                if parent_unit:
                    break
            
            if not parent_unit:
                raise NotFoundException(f"Parent unit {data.attached_to_unit_id} not found")
            
            if parent_unit.player_id != data.player_id:
                raise ValidationException("Cannot attach unit to a unit owned by another player")
        
        # Create the unit
        unit = Unit(
            player_id=player_id,  # Use cached value
            name=data.name,
            custom_name=data.custom_name,
            quality=data.quality,
            defense=data.defense,
            size=data.size,
            tough=props["tough"],
            cost=data.cost,
            loadout=data.loadout,
            rules=data.rules,
            is_hero=props["is_hero"],
            is_caster=props["is_caster"],
            caster_level=props["caster_level"],
            is_transport=props["is_transport"],
            transport_capacity=props["transport_capacity"],
            has_ambush=props["has_ambush"],
            has_scout=props["has_scout"],
            attached_to_unit_id=data.attached_to_unit_id,
        )
        session.add(unit)
        await session.flush()  # Get unit ID
        
        # Create initial state
        initial_deployment = (
            DeploymentStatus.IN_AMBUSH if props["has_ambush"]
            else DeploymentStatus.DEPLOYED
        )
        
        # Cache state values at creation time to avoid accessing after flush
        state_models_remaining = unit.size
        state_spell_tokens_val = props["caster_level"] if props["is_caster"] else 0
        
        state = UnitState(
            unit_id=unit.id,
            models_remaining=state_models_remaining,
            spell_tokens=state_spell_tokens_val,
            deployment_status=initial_deployment,
        )
        session.add(state)
        await session.flush()  # Get state ID
        
        # Cache all values IMMEDIATELY after flush, before any other operations
        # This is the only safe time to access these attributes
        # player_id already cached above
        unit_id = unit.id
        game_id = game.id
        game_round = game.current_round
        state_id = state.id  # Get ID right after flush, before commit
        
        # Update player stats
        player.starting_unit_count = (player.starting_unit_count or 0) + 1
        player.starting_points = (player.starting_points or 0) + data.cost
        
        # Log the unit creation
        # Cache all values before commit to avoid greenlet issues
        display_name = unit.display_name
        unit_name = unit.name  # Cache unit.name as well
        player_name = player.name  # Cache player.name as well
        
        # Create event directly to avoid accessing game/player objects in log_event
        event = GameEvent.create(
            game_id=game_id,
            event_type=EventType.CUSTOM,
            description=f"{player_name} added unit: {display_name} ({data.cost}pts)",
            player_id=player_id,
            round_number=game_round,
            target_unit_id=unit_id,
            details={
                "unit_name": unit_name,
                "cost": data.cost,
                "quality": data.quality,
                "defense": data.defense,
            },
        )
        session.add(event)
        
        await session.commit()
        
        # Use known initial values for state response (we just created it, so we know the values)
        # We cached state_id right after flush, so it's safe to use
        unit_state_response = UnitStateResponse(
            id=state_id,
            wounds_taken=0,  # Initial value
            models_remaining=state_models_remaining,
            activated_this_round=False,  # Initial value
            is_shaken=False,  # Initial value
            is_fatigued=False,  # Initial value
            deployment_status=initial_deployment,
            transport_id=None,  # Initial value
            spell_tokens=state_spell_tokens_val,
            limited_weapons_used=None,  # Initial value
            custom_notes=None,  # Initial value
        )
        
        unit_response = UnitResponse(
            id=unit_id,
            player_id=player_id,
            name=data.name,
            custom_name=data.custom_name,
            quality=data.quality,
            defense=data.defense,
            size=data.size,
            tough=props["tough"],
            cost=data.cost,
            loadout=data.loadout,
            rules=data.rules,
            is_hero=props["is_hero"],
            is_caster=props["is_caster"],
            caster_level=props["caster_level"],
            is_transport=props["is_transport"],
            transport_capacity=props["transport_capacity"],
            has_ambush=props["has_ambush"],
            has_scout=props["has_scout"],
            attached_to_unit_id=data.attached_to_unit_id,
            state=unit_state_response,
        )
        
        # Reload game to get is_solo flag
        game = await get_game_by_code(session, code)
        
        # Broadcast state update - skip for solo games
        await broadcast_if_not_solo(game, code, {
            "type": "state_update",
            "data": {
                "reason": "unit_created",
                "player_id": str(player_id),
                "unit_id": str(unit_id),
            }
        })
        
        return unit_response
    
    @delete("/{code:str}/players/{player_id:uuid}/units", status_code=200)
    async def clear_all_units(
        self,
        code: str,
        player_id: uuid.UUID,
        session: AsyncSession,
    ) -> ClearUnitsResponse:
        """Clear all units for a player (only allowed in lobby)."""
        game = await get_game_by_code(session, code, load_attached_heroes=True)
        
        # Only allow in lobby status
        if game.status != GameStatus.LOBBY:
            raise ValidationException("Units can only be cleared in the lobby")
        
        # Find the player
        player = None
        for p in game.players:
            if p.id == player_id:
                player = p
                break
        
        if not player:
            raise NotFoundException(f"Player {player_id} not found in game")
        
        # Query all units for this player
        units_stmt = select(Unit).where(Unit.player_id == player_id)
        units_result = await session.execute(units_stmt)
        units = units_result.scalars().all()
        
        units_count = len(units)
        total_points = sum(unit.cost for unit in units)
        
        # Cache values before deletion to avoid accessing expired objects
        player_name = player.name
        player_id_cached = player_id  # Already a parameter, but explicit
        game_id = game.id
        game_code = game.code
        game_round = game.current_round
        
        # Delete all units (cascade will handle UnitState deletion)
        for unit in units:
            await session.delete(unit)
        
        # Reset player stats
        player.starting_unit_count = 0
        player.starting_points = 0
        player.army_name = None
        player.army_forge_list_id = None
        
        # Log the clear action
        event = GameEvent.create(
            game_id=game_id,
            event_type=EventType.CUSTOM,
            description=f"{player_name} cleared all units ({units_count} units, {total_points}pts)",
            player_id=player_id_cached,
            round_number=game_round,
            details={
                "units_cleared": units_count,
                "points_cleared": total_points,
            },
        )
        session.add(event)
        
        await session.commit()
        
        # Reload game to get is_solo flag
        game = await get_game_by_code(session, game_code)
        
        # Broadcast state update - skip for solo games
        await broadcast_if_not_solo(game, game_code, {
            "type": "state_update",
            "data": {
                "reason": "units_cleared",
                "player_id": str(player_id_cached),
            }
        })
        
        return ClearUnitsResponse(
            success=True,
            units_cleared=units_count,
            message=f"Cleared {units_count} units ({total_points}pts)"
        )
    
    @patch("/{code:str}/units/{unit_id:uuid}/detach")
    async def detach_unit(
        self,
        code: str,
        unit_id: uuid.UUID,
        session: AsyncSession,
    ) -> UnitResponse:
        """Detach a hero unit from its parent unit."""
        game = await get_game_by_code(session, code, load_attached_heroes=True)
        
        # Find the unit
        unit = None
        for player in game.players:
            for u in player.units:
                if u.id == unit_id:
                    unit = u
                    break
        
        if not unit:
            raise NotFoundException(f"Unit {unit_id} not found in game")
        
        if not unit.attached_to_unit_id:
            raise ValidationException(f"{unit.display_name} is not attached to any unit")
        
        # Find parent unit for logging
        parent_unit = None
        for player in game.players:
            for u in player.units:
                if u.id == unit.attached_to_unit_id:
                    parent_unit = u
                    break
        
        parent_name = parent_unit.display_name if parent_unit else "unknown unit"
        
        # Detach the unit
        unit.attached_to_unit_id = None
        
        await log_event(
            session, game,
            EventType.UNIT_DETACHED,
            f"{unit.display_name} detached from {parent_name}",
            player_id=unit.player_id,
            target_unit_id=unit.id,
        )
        
        await session.commit()
        await session.refresh(unit)
        
        # Reload game to get is_solo flag
        game = await get_game_by_code(session, code)
        
        # Broadcast state update - skip for solo games
        await broadcast_if_not_solo(game, code, {
            "type": "state_update",
            "data": {
                "reason": "unit_detached",
                "unit_id": str(unit_id),
            }
        })
        
        return UnitResponse.model_validate(unit)
    
    @patch("/{code:str}/objectives/{objective_id:uuid}")
    async def update_objective(
        self,
        code: str,
        objective_id: uuid.UUID,
        data: UpdateObjectiveRequest,
        session: AsyncSession,
    ) -> ObjectiveResponse:
        """Update an objective's state."""
        game = await get_game_by_code(session, code)
        
        # Update activity tracking
        game.last_activity_at = datetime.now(timezone.utc)
        
        # Find the objective
        objective = None
        for obj in game.objectives:
            if obj.id == objective_id:
                objective = obj
                break
        
        if not objective:
            raise NotFoundException(f"Objective {objective_id} not found in game")
        
        old_status = objective.status
        objective.status = data.status
        objective.controlled_by_id = data.controlled_by_id
        
        # Log the change
        if data.status == ObjectiveStatus.SEIZED and data.controlled_by_id:
            # Find player name
            player_name = "Unknown"
            for p in game.players:
                if p.id == data.controlled_by_id:
                    player_name = p.name
                    break
            
            await log_event(
                session, game,
                EventType.OBJECTIVE_SEIZED,
                f"{player_name} seized {objective.display_name}",
                target_objective_id=objective.id,
                details={"previous_status": old_status.value},
            )
        elif data.status == ObjectiveStatus.CONTESTED:
            await log_event(
                session, game,
                EventType.OBJECTIVE_CONTESTED,
                f"{objective.display_name} is contested",
                target_objective_id=objective.id,
            )
        elif data.status == ObjectiveStatus.NEUTRAL:
            await log_event(
                session, game,
                EventType.OBJECTIVE_NEUTRALIZED,
                f"{objective.display_name} is now neutral",
                target_objective_id=objective.id,
            )
        
        await session.commit()
        await session.refresh(objective)
        
        # Reload game to get is_solo flag
        game = await get_game_by_code(session, code)
        
        # Broadcast state update to trigger event fetching on other clients - skip for solo games
        await broadcast_if_not_solo(game, code, {
            "type": "state_update",
            "data": {
                "reason": "objective_updated",
                "objective_id": str(objective_id),
            }
        })
        
        return ObjectiveResponse.model_validate(objective)
    
    @post("/{code:str}/objectives")
    async def create_objectives(
        self,
        code: str,
        data: CreateObjectivesRequest,
        session: AsyncSession,
    ) -> List[ObjectiveResponse]:
        """Create objective markers for a game."""
        game = await get_game_by_code(session, code)
        
        if game.objectives:
            raise ValidationException("Objectives already exist for this game")
        
        objectives = []
        for i in range(1, data.count + 1):
            obj = Objective(
                game_id=game.id,
                marker_number=i,
            )
            session.add(obj)
            objectives.append(obj)
        
        await session.commit()
        
        # Refresh to get IDs
        for obj in objectives:
            await session.refresh(obj)
        
        return [ObjectiveResponse.model_validate(obj) for obj in objectives]
    
    @get("/{code:str}/events")
    async def get_events(
        self,
        code: str,
        session: AsyncSession,
        limit: int = 50,
        offset: int = 0,
    ) -> List[GameEventResponse]:
        """Get game events (action log)."""
        game = await get_game_by_code(session, code)
        
        stmt = (
            select(GameEvent)
            .where(GameEvent.game_id == game.id)
            .where(GameEvent.is_undone == False)
            .order_by(GameEvent.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await session.execute(stmt)
        events = result.scalars().all()
        
        return [GameEventResponse.model_validate(e) for e in events]
    
    @get("/{code:str}/events/export")
    async def export_events(
        self,
        code: str,
        session: AsyncSession,
    ) -> Response:
        """Export game events as markdown."""
        game = await get_game_by_code(session, code)
        
        # Get all events (no limit for export)
        stmt = (
            select(GameEvent)
            .where(GameEvent.game_id == game.id)
            .where(GameEvent.is_undone == False)
            .order_by(GameEvent.created_at.asc())
        )
        result = await session.execute(stmt)
        events = result.scalars().all()
        
        # Format as markdown
        markdown = f"# Game Log: {game.name}\n\n"
        markdown += f"Game Code: {game.code}\n"
        markdown += f"Status: {game.status.value}\n"
        markdown += f"Exported: {datetime.now(timezone.utc).isoformat()}\n\n"
        markdown += "## Events\n\n"
        
        for event in events:
            timestamp = event.created_at.strftime("%Y-%m-%d %H:%M:%S")
            markdown += f"### Round {event.round_number} - {timestamp}\n"
            markdown += f"{event.description}\n\n"
        
        return Response(
            content=markdown,
            media_type="text/markdown",
            headers={
                "Content-Disposition": f'attachment; filename="game-{game.code}-events.md"'
            }
        )
    
    @patch("/{code:str}/players/{player_id:uuid}/victory-points")
    async def update_victory_points(
        self,
        code: str,
        player_id: uuid.UUID,
        data: UpdateVictoryPointsRequest,
        session: AsyncSession,
    ) -> PlayerResponse:
        """Update a player's victory points."""
        game = await get_game_by_code(session, code)
        
        # Update activity tracking
        game.last_activity_at = datetime.now(timezone.utc)
        
        # Find the player
        player = next((p for p in game.players if p.id == player_id), None)
        if not player:
            raise NotFoundException(f"Player {player_id} not found in game")
        
        # Store the VP before the change
        vp_before = player.victory_points
        
        # Update VP
        player.victory_points = max(0, player.victory_points + data.delta)  # Prevent negative VP
        
        if data.delta > 0:
            # Adding VP: Create one log entry for each point added
            for i in range(data.delta):
                vp_at_this_point = vp_before + i
                await log_event(
                    session, game,
                    EventType.VP_CHANGED,
                    f"{player.name} VP: {vp_at_this_point} → {vp_at_this_point + 1} (+1)",
                    player_id=player_id,
                    details={
                        "vp_before": vp_at_this_point,
                        "vp_after": vp_at_this_point + 1,
                        "delta": 1,
                    },
                )
        elif data.delta < 0:
            # Removing VP: Delete the most recent VP_CHANGED events (one per point removed)
            # This removes the corresponding "add" entries to reduce log clutter
            events_to_delete = abs(data.delta)
            stmt = (
                select(GameEvent)
                .where(GameEvent.game_id == game.id)
                .where(GameEvent.event_type == EventType.VP_CHANGED)
                .where(GameEvent.player_id == player_id)
                .where(GameEvent.is_undone == False)
                .order_by(GameEvent.created_at.desc())
                .limit(events_to_delete)
            )
            result = await session.execute(stmt)
            events_to_remove = result.scalars().all()
            
            for event in events_to_remove:
                await session.delete(event)
        
        await session.commit()
        await session.refresh(player)
        
        # Reload game to get is_solo flag
        game = await get_game_by_code(session, code)
        
        # Broadcast state update - skip for solo games
        await broadcast_if_not_solo(game, code, {
            "type": "state_update",
            "data": {
                "reason": "victory_points_updated",
                "player_id": str(player_id),
                "victory_points": player.victory_points,
            }
        })
        
        return PlayerResponse.model_validate(player)
    
    @patch("/{code:str}/round")
    async def update_round(
        self,
        code: str,
        data: UpdateRoundRequest,
        session: AsyncSession,
    ) -> GameResponse:
        """Update the game round."""
        game = await get_game_by_code(session, code)
        
        # Update activity tracking
        game.last_activity_at = datetime.now(timezone.utc)
        
        # Store the round before the change
        round_before = game.current_round
        
        # Update round (ensure it doesn't go below 1)
        new_round = max(1, game.current_round + data.delta)
        game.current_round = new_round
        
        # Log event or delete log entry
        if data.delta > 0:
            # Round increased: Create log entry
            await log_event(
                session, game,
                EventType.ROUND_STARTED,
                f"Round changed: {round_before} → {new_round} (+{data.delta})",
            )
        elif data.delta < 0:
            # Round decreased: Delete the most recent ROUND_STARTED event
            stmt = (
                select(GameEvent)
                .where(GameEvent.game_id == game.id)
                .where(GameEvent.event_type == EventType.ROUND_STARTED)
                .where(GameEvent.is_undone == False)
                .order_by(GameEvent.created_at.desc())
                .limit(1)
            )
            result = await session.execute(stmt)
            recent_round_event = result.scalar_one_or_none()
            
            if recent_round_event:
                await session.delete(recent_round_event)
        
        await session.commit()
        await session.refresh(game)
        
        # Broadcast state update - skip for solo games
        await broadcast_if_not_solo(game, code, {
            "type": "state_update",
            "data": {
                "reason": "round_updated",
                "current_round": new_round,
            }
        })
        
        return GameResponse.model_validate(game)
    
    @post("/{code:str}/save", status_code=201)
    async def save_game(
        self,
        code: str,
        data: SaveGameRequest,
        session: AsyncSession,
    ) -> SaveGameResponse:
        """Save current game state (solo mode only)."""
        game = await get_game_by_code(session, code, load_attached_heroes=True)
        
        if not game.is_solo:
            raise ValidationException("Save/load is only available for solo games")
        
        # Get full game state
        game_response = GameWithUnitsResponse.model_validate(game)
        
        # Serialize to JSON
        game_state_json = json.dumps(game_response.model_dump(), default=str)
        
        # Create save
        game_save = GameSave(
            game_id=game.id,
            save_name=data.save_name,
            description=data.description,
            game_state_json=game_state_json,
        )
        session.add(game_save)
        await session.flush()
        
        save_id = game_save.id
        await session.commit()
        
        # Log event
        await log_event(
            session, game,
            EventType.CUSTOM,
            f"Game saved: {data.save_name}",
            details={"save_id": str(save_id)},
        )
        await session.commit()
        
        return SaveGameResponse(
            success=True,
            save_id=save_id,
            save_name=data.save_name,
            message=f"Game saved as '{data.save_name}'"
        )
    
    @get("/{code:str}/saves")
    async def list_saves(
        self,
        code: str,
        session: AsyncSession,
    ) -> List[GameSaveResponse]:
        """List all saves for a game (solo mode only)."""
        game = await get_game_by_code(session, code)
        
        if not game.is_solo:
            raise ValidationException("Save/load is only available for solo games")
        
        stmt = (
            select(GameSave)
            .where(GameSave.game_id == game.id)
            .order_by(GameSave.saved_at.desc())
        )
        result = await session.execute(stmt)
        saves = result.scalars().all()
        
        return [GameSaveResponse.model_validate(save) for save in saves]
    
    @post("/{code:str}/load", status_code=200)
    async def load_game(
        self,
        code: str,
        data: LoadGameRequest,
        session: AsyncSession,
    ) -> GameWithUnitsResponse:
        """Load a saved game state (solo mode only)."""
        game = await get_game_by_code(session, code, load_attached_heroes=True)
        
        if not game.is_solo:
            raise ValidationException("Save/load is only available for solo games")
        
        # Get the save
        stmt = select(GameSave).where(
            GameSave.id == data.save_id,
            GameSave.game_id == game.id
        )
        result = await session.execute(stmt)
        game_save = result.scalar_one_or_none()
        
        if not game_save:
            raise NotFoundException(f"Save {data.save_id} not found for this game")
        
        # Deserialize game state
        saved_state = json.loads(game_save.game_state_json)
        
        # Log event
        await log_event(
            session, game,
            EventType.CUSTOM,
            f"Game loaded from save: {game_save.save_name}",
            details={"save_id": str(data.save_id)},
        )
        await session.commit()
        
        # Return current game state (full restore would require more complex logic)
        # For MVP, we'll just return the current state and note that full restore is a future enhancement
        game = await get_game_by_code(session, code, load_attached_heroes=True)
        units = []
        for p in game.players:
            units.extend(p.units)
        
        response = GameWithUnitsResponse.model_validate(game)
        response.units = [UnitResponse.model_validate(u) for u in units]
        return response