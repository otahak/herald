"""Game session API endpoints."""

import json
import logging
import random
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Any

from litestar import Controller, get, post, patch, delete, status_codes
from litestar.response import Response
from litestar.exceptions import NotFoundException, ValidationException, HTTPException
from sqlalchemy import select, delete as sql_delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    Game,
    GameSystem,
    GameStatus,
    Player,
    Unit,
    UnitState,
    DeploymentStatus,
    Objective,
    ObjectiveStatus,
    GameEvent,
    EventType,
    GameSave,
)
from app.api.websocket import broadcast_to_game
from app.utils.logging import error_log
from app.utils.unit_stats import get_effective_caster
from app.api.proxy import parse_special_rules
from app.api.game_schemas import (
    CreateGameRequest,
    JoinGameRequest,
    UpdateGameStateRequest,
    UpdateUnitStateRequest,
    LogUnitActionRequest,
    CastSpellRequest,
    UpdateUnitProfileRequest,
    UpdateObjectiveRequest,
    CreateObjectivesRequest,
    UpdateVictoryPointsRequest,
    UpdatePlayerNameRequest,
    UpdateRoundRequest,
    CreateUnitRequest,
    ClearUnitsResponse,
    SaveGameRequest,
    SaveGameResponse,
    GameSaveResponse,
    LoadGameRequest,
    PlayerResponse,
    UnitStateResponse,
    UnitResponse,
    ObjectiveResponse,
    GameEventResponse,
    GameResponse,
    GameWithUnitsResponse,
    JoinGameResponse,
)
from app.api.game_helpers import (
    get_game_by_code,
    check_and_update_expiration,
    log_event,
    broadcast_if_not_solo,
)
from app.utils.rate_limit import check_rate_limit

logger = logging.getLogger("Herald.games")


# Re-export for backwards compatibility (e.g. websocket may import get_game_by_code from here)
__all__ = ["GamesController", "get_game_by_code", "broadcast_if_not_solo", "log_event", "check_and_update_expiration"]


def _unit_response_with_effective_caster(unit: Unit) -> UnitResponse:
    """Build UnitResponse with is_caster/caster_level from DB or from rules/loadout/upgrades."""
    resp = UnitResponse.model_validate(unit)
    effective_caster, effective_level = get_effective_caster(unit)
    resp.is_caster = effective_caster
    if effective_caster:
        resp.caster_level = effective_level or resp.caster_level or 1
    return resp


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
            await session.commit()
        
        # Collect all units from all players
        units = []
        for player in game.players:
            units.extend(player.units)
        
        response = GameWithUnitsResponse.model_validate(game)
        response.units = [_unit_response_with_effective_caster(u) for u in units]
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
        response.units = [_unit_response_with_effective_caster(u) for u in units]
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
        response.units = [_unit_response_with_effective_caster(u) for u in units]
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
        
        return _unit_response_with_effective_caster(unit)
    
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
            upgrades=data.upgrades,
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
            upgrades=data.upgrades,
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
    
    @delete("/{code:str}/players/{player_id:uuid}/units", status_code=status_codes.HTTP_200_OK)
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
        
        return _unit_response_with_effective_caster(unit)
    
    @delete("/{code:str}/units/{unit_id:uuid}", status_code=status_codes.HTTP_200_OK)
    async def delete_unit(
        self,
        code: str,
        unit_id: uuid.UUID,
        session: AsyncSession,
    ) -> dict:
        """Delete a single unit (lobby only).
        
        Attached heroes on a deleted parent are detached (SET NULL), not cascade-deleted.
        """
        game = await get_game_by_code(session, code, load_attached_heroes=True)
        if game.status != GameStatus.LOBBY:
            raise ValidationException("Units can only be deleted during lobby")
        
        unit = None
        unit_player = None
        for player in game.players:
            for u in player.units:
                if u.id == unit_id:
                    unit = u
                    unit_player = player
                    break
        
        if not unit:
            raise NotFoundException(f"Unit {unit_id} not found in game")
        
        # Cache values before delete to avoid greenlet issues after commit
        unit_cost = unit.cost or 0
        display_name = unit.display_name
        unit_name = unit.name
        cached_unit_id = unit.id
        game_id = game.id
        game_round = game.current_round
        player_id = unit_player.id if unit_player else None
        player_name = unit_player.name if unit_player else "Unknown"
        
        if unit.state:
            await session.delete(unit.state)
        await session.delete(unit)
        
        if unit_player:
            unit_player.starting_unit_count = max(0, (unit_player.starting_unit_count or 0) - 1)
            unit_player.starting_points = max(0, (unit_player.starting_points or 0) - unit_cost)
        
        event = GameEvent.create(
            game_id=game_id,
            event_type=EventType.CUSTOM,
            description=f"{player_name} removed unit: {display_name} ({unit_cost}pts)",
            player_id=player_id,
            round_number=game_round,
            target_unit_id=cached_unit_id,
            details={
                "unit_name": unit_name,
                "cost": unit_cost,
            },
        )
        session.add(event)
        
        await session.commit()
        
        await broadcast_if_not_solo(game, code, {
            "type": "state_update",
            "data": {"reason": "unit_deleted", "unit_id": str(cached_unit_id)},
        })
        return {"success": True, "message": f"Unit deleted"}
    
    @patch("/{code:str}/units/{unit_id:uuid}/profile")
    async def update_unit_profile(
        self,
        code: str,
        unit_id: uuid.UUID,
        data: UpdateUnitProfileRequest,
        session: AsyncSession,
    ) -> UnitResponse:
        """Update a unit's profile fields like custom_name (lobby only)."""
        game = await get_game_by_code(session, code, load_attached_heroes=True)
        if game.status != GameStatus.LOBBY:
            raise ValidationException("Unit profile can only be edited during lobby")
        
        unit = None
        for player in game.players:
            for u in player.units:
                if u.id == unit_id:
                    unit = u
                    break
        
        if not unit:
            raise NotFoundException(f"Unit {unit_id} not found in game")
        
        if data.custom_name is not None:
            stripped = data.custom_name.strip()
            unit.custom_name = stripped if stripped else None
        
        await session.commit()
        await session.refresh(unit)
        
        await broadcast_if_not_solo(game, code, {
            "type": "state_update",
            "data": {"reason": "unit_updated", "unit_id": str(unit_id)},
        })
        return _unit_response_with_effective_caster(unit)
    
    @post("/{code:str}/units/{unit_id:uuid}/actions")
    async def log_unit_action(
        self,
        code: str,
        unit_id: uuid.UUID,
        data: LogUnitActionRequest,
        session: AsyncSession,
    ) -> dict:
        """Log a unit action (rush, advance, hold, charge, or attack)."""
        try:
            game = await get_game_by_code(session, code, load_attached_heroes=True)
        except Exception as e:
            logger.error(f"Error loading game {code}: {e}", exc_info=True)
            raise
        
        try:
            # Update activity tracking
            game.last_activity_at = datetime.now(timezone.utc)
            
            # Find the unit
            unit = None
            unit_player_id = None
            for player in game.players:
                for u in player.units:
                    if u.id == unit_id:
                        unit = u
                        unit_player_id = player.id
                        break
            
            if not unit:
                raise NotFoundException(f"Unit {unit_id} not found in game")
            
            if not unit.state:
                raise ValidationException("Unit has no state (not initialized)")
            
            # Shaken units can only Hold (idle to recover); cannot use other actions or cast
            if unit.state.is_shaken and data.action.lower() != "hold":
                raise ValidationException("Shaken units can only Hold (idle to recover).")
            
            # Validate action type
            valid_actions = ["rush", "advance", "hold", "charge", "attack"]
            if data.action.lower() not in valid_actions:
                raise ValidationException(f"Invalid action. Must be one of: {', '.join(valid_actions)}")
            
            action = data.action.lower()
            
            # For charge and attack, validate targets
            if action in ["charge", "attack"]:
                if not data.target_unit_ids or len(data.target_unit_ids) == 0:
                    raise ValidationException(f"{action.capitalize()} action requires at least one target unit")
                
                # Validate all target units exist and belong to opposing players
                target_units = []
                target_names = []
                for target_id in data.target_unit_ids:
                    # Convert target_id to UUID if it's a string (from frontend)
                    try:
                        target_uuid = target_id if isinstance(target_id, uuid.UUID) else uuid.UUID(str(target_id))
                    except (ValueError, TypeError) as e:
                        raise ValidationException(f"Invalid target unit ID format: {target_id}")
                    
                    target_found = False
                    for player in game.players:
                        if player.id == unit_player_id:
                            continue  # Skip the unit's own player
                        for u in player.units:
                            if u.id == target_uuid:
                                if u.state and u.state.deployment_status == DeploymentStatus.DESTROYED:
                                    raise ValidationException(f"Cannot target destroyed unit: {u.display_name}")
                                target_units.append(u)
                                target_names.append(u.display_name)
                                target_found = True
                                break
                        if target_found:
                            break
                    
                    if not target_found:
                        raise NotFoundException(f"Target unit {target_id} not found or belongs to same player")
            
            # Map action to EventType
            action_to_event = {
                "rush": EventType.UNIT_RUSHED,
                "advance": EventType.UNIT_ADVANCED,
                "hold": EventType.UNIT_HELD,
                "charge": EventType.UNIT_CHARGED,
                "attack": EventType.UNIT_ATTACKED,
            }
            
            event_type = action_to_event[action]
            
            # Build description
            if action in ["charge", "attack"]:
                target_names_str = ", ".join(target_names)
                # Fix past tense: charge -> charged, attack -> attacked
                action_past = "charged" if action == "charge" else "attacked"
                description = f"{unit.display_name} {action_past} {target_names_str}"
            else:
                action_past = {
                    "rush": "rushed",
                    "advance": "advanced",
                    "hold": "held position",
                }[action]
                description = f"{unit.display_name} {action_past}"
            
            # Prepare details with target unit IDs
            details = {}
            if action in ["charge", "attack"] and data.target_unit_ids:
                details["target_unit_ids"] = [str(tid) for tid in data.target_unit_ids]
                # Store first target as primary target_unit_id for reference
                # Ensure it's a UUID object
                first_target = data.target_unit_ids[0]
                primary_target_id = first_target if isinstance(first_target, uuid.UUID) else uuid.UUID(str(first_target))
            else:
                primary_target_id = None
            
            # Activate the unit before logging the action (so activation state is correct)
            if not unit.state.activated_this_round:
                unit.state.activated_this_round = True
                
                # When activating a unit, also activate any attached heroes
                # Check if attached_heroes relationship is loaded and has items
                try:
                    attached_heroes_list = list(unit.attached_heroes) if unit.attached_heroes else []
                except Exception:
                    # If relationship isn't loaded or accessible, skip
                    attached_heroes_list = []
                
                for attached_hero in attached_heroes_list:
                    if attached_hero.state and not attached_hero.state.activated_this_round:
                        attached_hero.state.activated_this_round = True
            
            # Log the action event (this replaces the separate "activated" event)
            await log_event(
                session, game,
                event_type,
                description,
                player_id=unit_player_id,
                target_unit_id=primary_target_id,
                details=details if details else None,
            )
            
            await session.commit()
            
            # Reload game to get is_solo flag
            game = await get_game_by_code(session, code)
            
            # Broadcast state update - skip for solo games
            await broadcast_if_not_solo(game, code, {
                "type": "state_update",
                "data": {
                    "reason": "unit_action_logged",
                    "unit_id": str(unit_id),
                    "action": action,
                }
            })
            
            return {"success": True, "message": description}
        except (NotFoundException, ValidationException) as e:
            # Re-raise validation/not found errors as-is (they're expected)
            raise
        except Exception as e:
            logger.error(f"Error logging unit action for unit {unit_id} in game {code}: {e}", exc_info=True)
            log_exception_with_context(
                "log_unit_action",
                {
                    "game_code": code,
                    "unit_id": str(unit_id),
                    "action": data.action,
                    "target_unit_ids": [str(tid) for tid in (data.target_unit_ids or [])],
                }
            )
            raise
    
    @post("/{code:str}/units/{unit_id:uuid}/cast")
    async def attempt_cast(
        self,
        code: str,
        unit_id: uuid.UUID,
        data: CastSpellRequest,
        session: AsyncSession,
    ) -> dict:
        """Attempt to cast a spell (during activation, before attacks). Caster(X): spend tokens >= spell value, roll 4+."""
        game = await get_game_by_code(session, code, load_attached_heroes=True)
        game.last_activity_at = datetime.now(timezone.utc)
        
        unit = None
        unit_player_id = None
        for player in game.players:
            for u in player.units:
                if u.id == unit_id:
                    unit = u
                    unit_player_id = player.id
                    break
        
        if not unit:
            raise NotFoundException(f"Unit {unit_id} not found in game")
        if not unit.state:
            raise ValidationException("Unit has no state")
        effective_caster, _ = get_effective_caster(unit)
        if not effective_caster:
            raise ValidationException("Unit is not a caster")
        if unit.state.is_shaken:
            raise ValidationException("Shaken units cannot cast spells")
        if unit.state.spell_tokens < data.spell_value:
            raise ValidationException(
                f"Not enough spell tokens: need {data.spell_value}, have {unit.state.spell_tokens}"
            )
        
        modifier = data.roll_modifier if data.roll_modifier is not None else 0
        roll = random.randint(1, 6)
        success = (roll + modifier) >= 4
        
        unit.state.spell_tokens -= data.spell_value
        
        spell_label = data.spell_name or f"Spell ({data.spell_value})"
        target_desc = ""
        if data.target_unit_id:
            for p in game.players:
                for u in p.units:
                    if u.id == data.target_unit_id:
                        target_desc = f" on {u.display_name}"
                        break
        
        result_desc = "succeeded" if success else "failed"
        mod_str = f"+{modifier}" if modifier > 0 else (f"{modifier}" if modifier < 0 else "")
        description = f"{unit.display_name} cast {spell_label}{target_desc}: roll {roll}{mod_str} → {result_desc}"
        
        await log_event(
            session,
            game,
            EventType.SPELL_CAST,
            description,
            player_id=unit_player_id,
            target_unit_id=data.target_unit_id,
            details={
                "spell_value": data.spell_value,
                "spell_name": data.spell_name,
                "roll": roll,
                "roll_modifier": modifier,
                "success": success,
                "tokens_remaining": unit.state.spell_tokens,
            },
        )
        await session.commit()
        
        await broadcast_if_not_solo(game, code, {
            "type": "state_update",
            "data": {
                "reason": "spell_cast",
                "unit_id": str(unit_id),
                "success": success,
            },
        })
        return {"success": success, "message": description, "roll": roll, "roll_modifier": modifier}
    
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
    
    @delete("/{code:str}/events", status_code=status_codes.HTTP_200_OK)
    async def clear_events(
        self,
        code: str,
        session: AsyncSession,
    ) -> dict:
        """Clear all events for a game."""
        if not check_rate_limit(f"clear_events:{code.upper()}", max_requests=5, window_sec=60):
            raise HTTPException(status_code=429, detail="Too many requests. Please try again in a minute.")
        game = await get_game_by_code(session, code)
        
        # Cache values before any operations that might cause greenlet issues
        game_id_value = game.id
        is_solo_value = game.is_solo
        
        # Update activity tracking
        game.last_activity_at = datetime.now(timezone.utc)
        
        # Get count and events to delete
        count_stmt = select(GameEvent).where(GameEvent.game_id == game_id_value)
        count_result = await session.execute(count_stmt)
        events_list = list(count_result.scalars().all())
        deleted_count = len(events_list)
        
        # Delete events using session.delete (same pattern as clear_all_units)
        # This avoids MissingGreenlet issues with bulk delete statements
        for event in events_list:
            await session.delete(event)
        
        await session.commit()
        
        # Reload game to get current state for broadcast
        game = await get_game_by_code(session, code)
        
        # Broadcast state update - skip for solo games
        await broadcast_if_not_solo(game, code, {
            "type": "state_update",
            "data": {
                "reason": "events_cleared",
            }
        })
        
        return {"success": True, "deleted_count": deleted_count}
    
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
    
    @patch("/{code:str}/players/{player_id:uuid}")
    async def update_player_name(
        self,
        code: str,
        player_id: uuid.UUID,
        data: UpdatePlayerNameRequest,
        session: AsyncSession,
    ) -> PlayerResponse:
        """Update a player's display name (solo mode only)."""
        game = await get_game_by_code(session, code)
        if not game.is_solo:
            raise ValidationException("Renaming players is only allowed in solo play mode")
        player = next((p for p in game.players if p.id == player_id), None)
        if not player:
            raise NotFoundException(f"Player {player_id} not found in game")
        game.last_activity_at = datetime.now(timezone.utc)
        new_name = data.name.strip()
        await log_event(
            session, game,
            EventType.CUSTOM,
            f"Renamed player to {new_name}",
            player_id=player_id,
        )
        player.name = new_name
        # Cache scalar fields and is_solo before commit to avoid lazy load after session commit
        out_data = {
            "id": player.id,
            "name": new_name,
            "color": player.color,
            "is_host": player.is_host,
            "is_connected": player.is_connected,
            "army_name": player.army_name,
            "starting_unit_count": player.starting_unit_count,
            "starting_points": player.starting_points,
            "victory_points": player.victory_points,
        }
        is_solo = game.is_solo
        await session.commit()
        if not is_solo:
            await broadcast_to_game(code, {
                "type": "state_update",
                "data": {"reason": "player_renamed", "player_id": str(player_id), "name": new_name},
            })
        return PlayerResponse.model_validate(out_data)
    
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
            # New round: reset activations and grant caster spell tokens
            for player in game.players:
                player.has_finished_activations = False
                for unit in player.units:
                    if unit.state:
                        unit.state.reset_for_new_round()
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
        
        # Get full game state (include units from all players, same as get_game)
        game_response = GameWithUnitsResponse.model_validate(game)
        units = []
        for p in game.players:
            units.extend(p.units)
        game_response.units = [_unit_response_with_effective_caster(u) for u in units]
        
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
        
        # Log event before commit (game object is still valid)
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
        
        def _uuid(v):
            if v is None:
                return None
            if isinstance(v, uuid.UUID):
                return v
            return uuid.UUID(str(v))
        
        # 1. Restore game-level fields
        game.status = GameStatus(saved_state["status"])
        game.current_round = int(saved_state["current_round"])
        game.max_rounds = int(saved_state["max_rounds"])
        game.current_player_id = _uuid(saved_state.get("current_player_id"))
        game.first_player_next_round_id = _uuid(saved_state.get("first_player_next_round_id"))
        
        # 2. Restore player fields (same players, update stats)
        player_by_id = {p.id: p for p in game.players}
        for sp in saved_state.get("players", []):
            pid = _uuid(sp.get("id"))
            if pid and pid in player_by_id:
                p = player_by_id[pid]
                p.name = sp.get("name", p.name)
                p.color = sp.get("color", p.color)
                p.victory_points = int(sp.get("victory_points", 0))
                p.starting_unit_count = int(sp.get("starting_unit_count", 0))
                p.starting_points = int(sp.get("starting_points", 0))
                p.army_name = sp.get("army_name") or None
        
        # 3. Delete existing units (and their states via cascade)
        await session.execute(sql_delete(Unit).where(Unit.player_id.in_([p.id for p in game.players])))
        await session.flush()
        
        # 4. Recreate units and states from save; map old unit id -> new unit for attachments
        old_id_to_unit: dict[str, Unit] = {}
        old_id_to_state: dict[str, UnitState] = {}
        for su in saved_state.get("units", []):
            new_unit = Unit(
                player_id=_uuid(su["player_id"]),
                name=su.get("name", "Unit"),
                custom_name=su.get("custom_name"),
                quality=int(su.get("quality", 4)),
                defense=int(su.get("defense", 4)),
                size=int(su.get("size", 1)),
                tough=int(su.get("tough", 1)),
                cost=int(su.get("cost", 0)),
                loadout=su.get("loadout"),
                rules=su.get("rules"),
                upgrades=su.get("upgrades"),
                is_hero=bool(su.get("is_hero", False)),
                is_caster=bool(su.get("is_caster", False)),
                caster_level=int(su.get("caster_level", 0)),
                is_transport=bool(su.get("is_transport", False)),
                transport_capacity=int(su.get("transport_capacity", 0)),
                has_ambush=bool(su.get("has_ambush", False)),
                has_scout=bool(su.get("has_scout", False)),
                attached_to_unit_id=None,
            )
            session.add(new_unit)
            await session.flush()
            old_id_to_unit[str(su["id"])] = new_unit
            
            sstate = su.get("state")
            if sstate is not None:
                state = UnitState(
                    unit_id=new_unit.id,
                    wounds_taken=int(sstate.get("wounds_taken", 0)),
                    models_remaining=int(sstate.get("models_remaining", new_unit.size)),
                    activated_this_round=bool(sstate.get("activated_this_round", False)),
                    is_shaken=bool(sstate.get("is_shaken", False)),
                    is_fatigued=bool(sstate.get("is_fatigued", False)),
                    deployment_status=DeploymentStatus(sstate.get("deployment_status", "deployed")),
                    transport_id=None,  # set below after all units exist
                    spell_tokens=int(sstate.get("spell_tokens", 0)),
                    limited_weapons_used=sstate.get("limited_weapons_used"),
                    custom_notes=sstate.get("custom_notes"),
                )
                session.add(state)
                old_id_to_state[str(su["id"])] = state
        await session.flush()
        
        # 5. Set attached_to_unit_id and transport_id (map old unit ids -> new)
        for su in saved_state.get("units", []):
            new_unit = old_id_to_unit.get(str(su["id"]))
            if not new_unit:
                continue
            old_attached = su.get("attached_to_unit_id")
            if old_attached:
                new_parent = old_id_to_unit.get(str(old_attached))
                if new_parent:
                    new_unit.attached_to_unit_id = new_parent.id
            sstate = su.get("state")
            if sstate and sstate.get("transport_id"):
                new_transport = old_id_to_unit.get(str(sstate["transport_id"]))
                state = old_id_to_state.get(str(su["id"]))
                if new_transport and state:
                    state.transport_id = new_transport.id
        await session.flush()
        
        # 6. Restore objectives
        obj_by_id = {o.id: o for o in game.objectives}
        for so in saved_state.get("objectives", []):
            oid = _uuid(so.get("id"))
            if oid and oid in obj_by_id:
                o = obj_by_id[oid]
                o.status = ObjectiveStatus(so.get("status", "neutral"))
                o.controlled_by_id = _uuid(so.get("controlled_by_id"))
        
        # Log event and commit
        await log_event(
            session, game,
            EventType.CUSTOM,
            f"Game loaded from save: {game_save.save_name}",
            details={"save_id": str(data.save_id)},
        )
        await session.commit()
        
        # Return restored state
        game = await get_game_by_code(session, code, load_attached_heroes=True)
        units = []
        for p in game.players:
            units.extend(p.units)
        response = GameWithUnitsResponse.model_validate(game)
        response.units = [_unit_response_with_effective_caster(u) for u in units]
        return response