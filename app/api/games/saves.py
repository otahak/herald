"""Solo save/load API."""

import json
import uuid
from datetime import datetime, timezone
from typing import List

from litestar import Controller, get, post, status_codes
from litestar.exceptions import NotFoundException, ValidationException
from sqlalchemy import delete as sql_delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.game_helpers import broadcast_if_not_solo, get_game_by_code, log_event
from app.api.game_schemas import (
    GameResponse,
    GameSaveResponse,
    GameWithUnitsResponse,
    LoadGameRequest,
    SaveGameRequest,
    SaveGameResponse,
)
from app.api.games.common import unit_response_with_effective_caster
from app.models import (
    DeploymentStatus,
    EventType,
    GameSave,
    GameStatus,
    ObjectiveStatus,
    Unit,
    UnitState,
)

class GamesSavesController(Controller):
    """Persist and restore full solo game snapshots."""

    path = "/api/games"
    tags = ["games", "games-saves"]

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
        game_response.units = [unit_response_with_effective_caster(u) for u in units]
        
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
                p.army_forge_list_id = sp.get("army_forge_list_id") or None
                p.has_finished_activations = bool(sp.get("has_finished_activations", False))
                p.spells = sp.get("spells") or None
                p.special_rules = sp.get("special_rules") or None
                p.faction_name = sp.get("faction_name") or None
                p.army_book_version = sp.get("army_book_version") or None
        
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
                continue  # pragma: no cover
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
        response.units = [unit_response_with_effective_caster(u) for u in units]
        return response
