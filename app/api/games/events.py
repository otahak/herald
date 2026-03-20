"""Game event log API."""

from datetime import datetime, timezone
from typing import List

from litestar import Controller, delete, get, status_codes
from litestar.exceptions import HTTPException
from litestar.response import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.game_helpers import broadcast_if_not_solo, get_game_by_code
from app.api.game_schemas import GameEventResponse
from app.models import GameEvent
from app.utils.rate_limit import check_rate_limit


class GamesEventsController(Controller):
    """Action log: list, export, clear."""

    path = "/api/games"
    tags = ["games", "games-events"]

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
