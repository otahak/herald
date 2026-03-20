"""Game units API: activation actions and spell casts."""

import logging
import uuid
from datetime import datetime, timezone

from litestar import Controller, post
from litestar.exceptions import NotFoundException, ValidationException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.game_helpers import broadcast_if_not_solo, get_game_by_code, log_event
from app.api.game_schemas import CastSpellRequest, LogUnitActionRequest
from app.models import DeploymentStatus, EventType
from app.utils.logging import log_exception_with_context
from app.utils.unit_stats import get_effective_caster

logger = logging.getLogger("Herald.games.units_combat")


class GamesUnitsCombatController(Controller):
    """Unit action log and spell resolution (same URL prefix as other games routes)."""

    path = "/api/games"
    tags = ["games", "games-units-combat"]

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
                    except (ValueError, TypeError) as e:  # pragma: no cover
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
                e,
                context={
                    "game_code": code,
                    "unit_id": str(unit_id),
                    "action": data.action,
                    "target_unit_ids": [str(tid) for tid in (data.target_unit_ids or [])],
                },
                message="log_unit_action",
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
        """Record a spell cast result. Player rolls dice themselves; we just track the outcome."""
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
        
        unit.state.spell_tokens -= data.spell_value
        
        spell_label = data.spell_name or f"Spell ({data.spell_value})"
        target_desc = ""
        if data.target_unit_id:
            for p in game.players:
                for u in p.units:
                    if u.id == data.target_unit_id:
                        target_desc = f" on {u.display_name}"
                        break
        
        result_desc = "succeeded" if data.success else "failed"
        description = f"{unit.display_name} cast {spell_label}{target_desc} — {result_desc}"
        
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
                "success": data.success,
                "tokens_remaining": unit.state.spell_tokens,
            },
        )
        await session.commit()
        
        game = await get_game_by_code(session, code)
        
        await broadcast_if_not_solo(game, code, {
            "type": "state_update",
            "data": {
                "reason": "spell_cast",
                "unit_id": str(unit_id),
                "success": data.success,
            },
        })
        return {"success": data.success, "message": description}
