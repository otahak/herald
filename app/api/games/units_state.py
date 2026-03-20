"""Game units API: state patches and lobby unit management."""

import logging
import uuid
from datetime import datetime, timezone

from litestar import Controller, delete, patch, post, status_codes
from litestar.exceptions import HTTPException, NotFoundException, ValidationException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.game_helpers import broadcast_if_not_solo, get_game_by_code, log_event
from app.api.game_schemas import (
    CastSpellRequest,
    ClearUnitsResponse,
    CreateUnitRequest,
    LogUnitActionRequest,
    UnitResponse,
    UnitStateResponse,
    UpdateUnitProfileRequest,
    UpdateUnitStateRequest,
)
from app.api.games.common import unit_response_with_effective_caster
from app.api.websocket import broadcast_to_game
from app.army_forge.parse import parse_special_rules
from app.models import (
    DeploymentStatus,
    EventType,
    GameEvent,
    GameStatus,
    Player,
    Unit,
    UnitState,
)
from app.services.games.errors import UnitStateValidationError
from app.services.games.unit_state import apply_update_unit_state
from app.utils.logging import log_exception_with_context
from app.utils.unit_stats import get_effective_caster

logger = logging.getLogger("Herald.games.units_state")


class GamesUnitsStateController(Controller):
    """Unit state, manual create/clear, detach, delete, profile."""

    path = "/api/games"
    tags = ["games", "games-units"]

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
        game.last_activity_at = datetime.now(timezone.utc)

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

        try:
            await apply_update_unit_state(session, game, unit, unit_id, data)
        except UnitStateValidationError as e:
            raise ValidationException(detail=str(e)) from e

        await session.commit()
        await session.refresh(unit)

        game = await get_game_by_code(session, code)
        await broadcast_if_not_solo(
            game,
            code,
            {
                "type": "state_update",
                "data": {
                    "reason": "unit_updated",
                    "unit_id": str(unit_id),
                },
            },
        )

        return unit_response_with_effective_caster(unit)
    
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
        
        # Reset player stats and army book data
        player.starting_unit_count = 0
        player.starting_points = 0
        player.army_name = None
        player.army_forge_list_id = None
        player.spells = None
        player.special_rules = None
        player.faction_name = None
        player.army_book_version = None
        
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
        
        return unit_response_with_effective_caster(unit)
    
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
        is_solo = game.is_solo
        
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
        
        if not is_solo:
            await broadcast_to_game(code, {
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
        
        game = await get_game_by_code(session, code)
        
        await broadcast_if_not_solo(game, code, {
            "type": "state_update",
            "data": {"reason": "unit_updated", "unit_id": str(unit_id)},
        })
        return unit_response_with_effective_caster(unit)
    
