"""Game session API endpoints."""

import logging
import uuid
from datetime import datetime
from typing import Optional, List, Any

from litestar import Controller, get, post, patch, delete
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
)
from app.api.websocket import broadcast_to_game

logger = logging.getLogger("Herald.games")


# --- Request/Response Schemas ---

class CreateGameRequest(BaseModel):
    """Request to create a new game."""
    name: str = Field(default="New Game", max_length=100)
    game_system: GameSystem = GameSystem.GFF
    player_name: str = Field(max_length=50)
    player_color: str = Field(default="#3b82f6", max_length=20)


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
    count: int = Field(ge=3, le=5, default=4)


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

async def get_game_by_code(session: AsyncSession, code: str) -> Game:
    """Fetch game by join code with relationships loaded."""
    stmt = (
        select(Game)
        .where(Game.code == code.upper())
        .options(
            selectinload(Game.players).selectinload(Player.units).selectinload(Unit.state),
            selectinload(Game.objectives),
        )
    )
    result = await session.execute(stmt)
    game = result.scalar_one_or_none()
    if not game:
        raise NotFoundException(f"Game with code '{code}' not found")
    return game


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
            # Create game
            game = Game(
                name=data.name,
                game_system=data.game_system,
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
            logger.error(f"Failed to create game: {str(e)}")
            raise
    
    @get("/{code:str}")
    async def get_game(
        self,
        code: str,
        session: AsyncSession,
    ) -> GameWithUnitsResponse:
        """Get game state by join code."""
        game = await get_game_by_code(session, code)
        
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
        
        # Broadcast to WebSocket clients (notify host)
        await broadcast_to_game(code, {
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
        
        if game.status != GameStatus.LOBBY:
            raise ValidationException("Game has already started")
        
        if len(game.players) < 2:
            raise ValidationException("Need at least 2 players to start")
        
        # Check both players have units
        for player in game.players:
            if not player.units:
                raise ValidationException(f"Player {player.name} has no units")
        
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
        
        # Broadcast to WebSocket clients
        await broadcast_to_game(code, {
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
        
        # Broadcast state update to other clients
        await broadcast_to_game(code, {
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
        game = await get_game_by_code(session, code)
        
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
                changes.append(f"took {wound_diff} wound(s)")
                await log_event(
                    session, game,
                    EventType.UNIT_WOUNDED,
                    f"{unit.display_name} took {wound_diff} wound(s) ({unit.state.wounds_remaining}/{unit.max_wounds} remaining)",
                    target_unit_id=unit.id,
                    details={"wounds": wound_diff, "wounds_before": previous_state["wounds_taken"], "wounds_after": data.wounds_taken},
                    previous_state=previous_state,
                )
            else:
                changes.append(f"healed {-wound_diff} wound(s)")
                await log_event(
                    session, game,
                    EventType.UNIT_HEALED,
                    f"{unit.display_name} healed {-wound_diff} wound(s)",
                    target_unit_id=unit.id,
                    details={"wounds_healed": -wound_diff},
                    previous_state=previous_state,
                )
        
        if data.models_remaining is not None:
            unit.state.models_remaining = data.models_remaining
        
        if data.activated_this_round is not None and data.activated_this_round != unit.state.activated_this_round:
            unit.state.activated_this_round = data.activated_this_round
            if data.activated_this_round:
                await log_event(
                    session, game,
                    EventType.UNIT_ACTIVATED,
                    f"{unit.display_name} activated",
                    target_unit_id=unit.id,
                )
        
        if data.is_shaken is not None and data.is_shaken != unit.state.is_shaken:
            unit.state.is_shaken = data.is_shaken
            if data.is_shaken:
                await log_event(
                    session, game,
                    EventType.STATUS_SHAKEN,
                    f"{unit.display_name} became Shaken",
                    target_unit_id=unit.id,
                )
            else:
                await log_event(
                    session, game,
                    EventType.STATUS_SHAKEN_CLEARED,
                    f"{unit.display_name} is no longer Shaken",
                    target_unit_id=unit.id,
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
