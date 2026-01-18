"""Game session model."""

import enum
import secrets
import string
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import String, Integer, Enum, ForeignKey, Boolean, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.player import Player
    from app.models.objective import Objective
    from app.models.event import GameEvent


class GameSystem(str, enum.Enum):
    """Supported game systems."""
    GF = "gf"           # Grimdark Future (army scale)
    GFF = "gff"         # Grimdark Future: Firefight (skirmish)


class GameStatus(str, enum.Enum):
    """Game session status."""
    LOBBY = "lobby"           # Waiting for players, setting up armies
    IN_PROGRESS = "in_progress"  # Game is being played
    PAUSED = "paused"         # Game paused (players can rejoin)
    COMPLETED = "completed"   # Game finished
    EXPIRED = "expired"       # Game expired due to inactivity


def generate_join_code(length: int = 6) -> str:
    """Generate a random alphanumeric join code."""
    alphabet = string.ascii_uppercase + string.digits
    # Exclude ambiguous characters
    alphabet = alphabet.replace("0", "").replace("O", "").replace("I", "").replace("1", "")
    return "".join(secrets.choice(alphabet) for _ in range(length))


class Game(Base):
    """A game session between players."""
    
    __tablename__ = "games"
    
    # Join code for players to connect (e.g., "ABC123")
    code: Mapped[str] = mapped_column(
        String(10),
        unique=True,
        index=True,
        default=generate_join_code,
    )
    
    # Game metadata
    name: Mapped[str] = mapped_column(String(100), default="New Game")
    game_system: Mapped[GameSystem] = mapped_column(
        Enum(GameSystem),
        default=GameSystem.GFF,
    )
    status: Mapped[GameStatus] = mapped_column(
        Enum(GameStatus),
        default=GameStatus.LOBBY,
    )
    
    # Solo play mode (single player controls both armies)
    is_solo: Mapped[bool] = mapped_column(Boolean, default=False)
    
    # Activity tracking for expiration
    last_activity_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    
    # Turn tracking
    current_round: Mapped[int] = mapped_column(Integer, default=1)
    max_rounds: Mapped[int] = mapped_column(Integer, default=4)
    
    # Who's turn is it? (player_id of active player)
    current_player_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("players.id", ondelete="SET NULL"),
        nullable=True,
    )
    
    # Who goes first next round? (tracks who finished activating first)
    first_player_next_round_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("players.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
    )
    
    # Relationships
    players: Mapped[List["Player"]] = relationship(
        "Player",
        back_populates="game",
        foreign_keys="Player.game_id",
        cascade="all, delete-orphan",
    )
    objectives: Mapped[List["Objective"]] = relationship(
        "Objective",
        back_populates="game",
        cascade="all, delete-orphan",
    )
    events: Mapped[List["GameEvent"]] = relationship(
        "GameEvent",
        back_populates="game",
        cascade="all, delete-orphan",
        order_by="GameEvent.created_at",
    )
    
    def __repr__(self) -> str:
        return f"<Game {self.code} ({self.game_system.value}) - {self.status.value}>"
