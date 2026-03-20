"""Objectives API."""

import uuid
from datetime import datetime, timezone
from typing import List

from litestar import Controller, patch, post
from litestar.exceptions import NotFoundException, ValidationException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.game_helpers import broadcast_if_not_solo, get_game_by_code, log_event
from app.api.game_schemas import CreateObjectivesRequest, ObjectiveResponse, UpdateObjectiveRequest
from app.models import EventType, Objective, ObjectiveStatus


class GamesObjectivesController(Controller):
    """Objective markers and control state."""

    path = "/api/games"
    tags = ["games", "games-objectives"]

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
