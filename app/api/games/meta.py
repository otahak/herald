"""Players, VP, and round API."""

import uuid
from datetime import datetime, timezone

from litestar import Controller, patch
from litestar.exceptions import NotFoundException, ValidationException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.game_helpers import broadcast_if_not_solo, get_game_by_code, log_event
from app.api.game_schemas import (
    GameResponse,
    PlayerResponse,
    UpdatePlayerNameRequest,
    UpdateRoundRequest,
    UpdateVictoryPointsRequest,
)
from app.models import EventType, GameEvent, GameStatus


class GamesMetaController(Controller):
    """Victory points, player rename (solo), round delta."""

    path = "/api/games"
    tags = ["games", "games-meta"]

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
        # Cache scalar fields before commit to avoid lazy load after session commit
        out_data = {
            "id": player.id,
            "name": new_name,
            "color": player.color,
            "is_host": player.is_host,
            "is_connected": player.is_connected,
            "army_name": player.army_name,
            "army_forge_list_id": player.army_forge_list_id,
            "starting_unit_count": player.starting_unit_count,
            "starting_points": player.starting_points,
            "victory_points": player.victory_points,
            "has_finished_activations": player.has_finished_activations,
            "spells": player.spells,
            "special_rules": player.special_rules,
            "faction_name": player.faction_name,
            "army_book_version": player.army_book_version,
        }
        await session.commit()
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
    
