"""Admin API endpoints."""

import logging
from datetime import datetime, timedelta
from typing import List, Optional

from litestar import Controller, get, patch
from litestar.exceptions import HTTPException
from litestar.status_codes import HTTP_500_INTERNAL_SERVER_ERROR
from pydantic import BaseModel
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.models import Game, Player, Unit, GameEvent, Feedback
from app.models.game import GameStatus
from app.auth.oauth import require_admin_guard

logger = logging.getLogger("Herald.admin")


# --- Response Schemas ---

class FeedbackListItem(BaseModel):
    """Feedback list item."""
    id: str
    name: str
    email: str
    message: str
    read: bool
    created_at: datetime


class StatsResponse(BaseModel):
    """Admin statistics."""
    total_games: int
    active_games: int
    total_players: int
    total_units: int
    total_feedback: int
    unread_feedback: int
    games_last_24h: int
    games_last_7d: int


class RecentEvent(BaseModel):
    """Recent game event."""
    id: str
    event_type: str
    description: str
    game_code: Optional[str]
    created_at: datetime


# --- Controller ---

class AdminController(Controller):
    """API endpoints for admin dashboard."""
    
    path = "/api/admin"
    tags = ["admin"]
    guards = [require_admin_guard]
    
    @get("/feedback")
    async def get_feedback(
        self,
        session: AsyncSession,
        unread_only: bool = False,
    ) -> List[FeedbackListItem]:
        """Get all feedback submissions."""
        try:
            stmt = select(Feedback).order_by(desc(Feedback.created_at))
            
            if unread_only:
                stmt = stmt.where(Feedback.read == False)
            
            result = await session.execute(stmt)
            feedback_list = result.scalars().all()
            
            return [
                FeedbackListItem(
                    id=str(f.id),
                    name=f.name,
                    email=f.email,
                    message=f.message,
                    read=f.read,
                    created_at=f.created_at,
                )
                for f in feedback_list
            ]
        except (OperationalError, ProgrammingError) as e:
            logger.exception(f"Database error fetching feedback: {e}")
            # Check if it's a missing table error
            error_msg = str(e).lower()
            if "does not exist" in error_msg or "no such table" in error_msg:
                raise HTTPException(
                    detail="Feedback table does not exist. Please run database migrations.",
                    status_code=HTTP_500_INTERNAL_SERVER_ERROR
                )
            raise HTTPException(
                detail=f"Database error: {str(e)}",
                status_code=HTTP_500_INTERNAL_SERVER_ERROR
            )
        except Exception as e:
            logger.exception(f"Error fetching feedback: {e}")
            raise HTTPException(
                detail=f"Error fetching feedback: {str(e)}",
                status_code=HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @patch("/feedback/{feedback_id:uuid}/read")
    async def mark_feedback_read(
        self,
        feedback_id: str,
        session: AsyncSession,
    ) -> dict:
        """Mark feedback as read."""
        result = await session.execute(
            select(Feedback).where(Feedback.id == feedback_id)
        )
        feedback = result.scalar_one_or_none()
        
        if not feedback:
            return {"success": False, "message": "Feedback not found"}
        
        feedback.read = True
        await session.commit()
        
        return {"success": True}
    
    @get("/stats")
    async def get_stats(
        self,
        session: AsyncSession,
    ) -> StatsResponse:
        """Get admin statistics."""
        try:
            # Total games
            total_games_result = await session.execute(
                select(func.count(Game.id))
            )
            total_games = total_games_result.scalar() or 0
            
            # Active games (status = IN_PROGRESS)
            active_games_result = await session.execute(
                select(func.count(Game.id)).where(Game.status == GameStatus.IN_PROGRESS)
            )
            active_games = active_games_result.scalar() or 0
            
            # Total players
            total_players_result = await session.execute(
                select(func.count(Player.id))
            )
            total_players = total_players_result.scalar() or 0
            
            # Total units
            total_units_result = await session.execute(
                select(func.count(Unit.id))
            )
            total_units = total_units_result.scalar() or 0
            
            # Total feedback
            total_feedback_result = await session.execute(
                select(func.count(Feedback.id))
            )
            total_feedback = total_feedback_result.scalar() or 0
            
            # Unread feedback
            unread_feedback_result = await session.execute(
                select(func.count(Feedback.id)).where(Feedback.read == False)
            )
            unread_feedback = unread_feedback_result.scalar() or 0
            
            # Games created in last 24 hours
            day_ago = datetime.utcnow() - timedelta(days=1)
            games_24h_result = await session.execute(
                select(func.count(Game.id)).where(Game.created_at >= day_ago)
            )
            games_last_24h = games_24h_result.scalar() or 0
            
            # Games created in last 7 days
            week_ago = datetime.utcnow() - timedelta(days=7)
            games_7d_result = await session.execute(
                select(func.count(Game.id)).where(Game.created_at >= week_ago)
            )
            games_last_7d = games_7d_result.scalar() or 0
            
            return StatsResponse(
                total_games=total_games,
                active_games=active_games,
                total_players=total_players,
                total_units=total_units,
                total_feedback=total_feedback,
                unread_feedback=unread_feedback,
                games_last_24h=games_last_24h,
                games_last_7d=games_last_7d,
            )
        except (OperationalError, ProgrammingError) as e:
            logger.exception(f"Database error fetching stats: {e}")
            # Check if it's a missing table error
            error_msg = str(e).lower()
            if "does not exist" in error_msg or "no such table" in error_msg:
                raise HTTPException(
                    detail="Database tables do not exist. Please run database migrations.",
                    status_code=HTTP_500_INTERNAL_SERVER_ERROR
                )
            raise HTTPException(
                detail=f"Database error: {str(e)}",
                status_code=HTTP_500_INTERNAL_SERVER_ERROR
            )
        except Exception as e:
            logger.exception(f"Error fetching stats: {e}")
            raise HTTPException(
                detail=f"Error fetching stats: {str(e)}",
                status_code=HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @get("/events/recent")
    async def get_recent_events(
        self,
        session: AsyncSession,
        limit: int = 50,
    ) -> List[RecentEvent]:
        """Get recent game events."""
        stmt = (
            select(GameEvent, Game.code)
            .join(Game, GameEvent.game_id == Game.id)
            .order_by(desc(GameEvent.created_at))
            .limit(limit)
        )
        
        result = await session.execute(stmt)
        events = result.all()
        
        return [
            RecentEvent(
                id=str(event.id),
                event_type=event.event_type.value,
                description=event.description,
                game_code=code,
                created_at=event.created_at,
            )
            for event, code in events
        ]
