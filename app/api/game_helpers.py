"""Shared helpers for game API: fetch game, expiration, logging, broadcast."""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from litestar.exceptions import NotFoundException

from app.models import (
    Game,
    GameStatus,
    Player,
    Unit,
    Objective,
    GameEvent,
    EventType,
)
from app.api.websocket import broadcast_to_game


async def broadcast_if_not_solo(game: Game, code: str, message: dict) -> None:
    """Broadcast to game only if not in solo mode."""
    if not game.is_solo:
        await broadcast_to_game(code, message)


async def get_game_by_code(
    session: AsyncSession,
    code: str,
    load_attached_heroes: bool = False,
) -> Game:
    """Fetch game by join code with relationships loaded."""
    stmt = (
        select(Game)
        .where(Game.code == code.upper())
        .options(
            selectinload(Game.players).selectinload(Player.units).selectinload(Unit.state),
            selectinload(Game.objectives),
        )
    )
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
    Returns True if game is expired, False otherwise.
    """
    if game.status == GameStatus.EXPIRED:
        return True
    now = datetime.now(timezone.utc)
    if not game.last_activity_at:
        return False
    if game.is_solo:
        if now - game.last_activity_at > timedelta(days=30):
            game.status = GameStatus.EXPIRED
            return True
    else:
        all_disconnected = all(not p.is_connected for p in game.players) if game.players else True
        if all_disconnected and now - game.last_activity_at > timedelta(hours=1):
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
