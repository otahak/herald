"""Objective marker model."""

import enum
import uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import String, Integer, ForeignKey, Enum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.game import Game
    from app.models.player import Player


class ObjectiveStatus(str, enum.Enum):
    """Objective marker state."""
    NEUTRAL = "neutral"       # Not controlled by anyone
    SEIZED = "seized"         # Controlled by a player
    CONTESTED = "contested"   # Multiple players contesting


class Objective(Base):
    """
    An objective marker on the battlefield.
    
    Games have D3+2 (3-5) objective markers. At the end of each round,
    markers are checked for control:
    - Seized: One player's unit within 3", no enemies within 3"
    - Contested: Both players have units within 3"
    - Neutral: No units within 3", or was contested
    """
    
    __tablename__ = "objectives"
    
    # Which game this objective belongs to
    game_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"),
        index=True,
    )
    
    # Marker identification (1, 2, 3, etc.)
    marker_number: Mapped[int] = mapped_column(Integer)
    
    # Optional label for thematic objectives
    label: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    
    # Current state
    status: Mapped[ObjectiveStatus] = mapped_column(
        Enum(ObjectiveStatus),
        default=ObjectiveStatus.NEUTRAL,
    )
    
    # Who controls it (only set when status is SEIZED)
    controlled_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("players.id", ondelete="SET NULL"),
        nullable=True,
    )
    
    # Relationships
    game: Mapped["Game"] = relationship("Game", back_populates="objectives")
    controlled_by_player: Mapped[Optional["Player"]] = relationship(
        "Player",
        back_populates="controlled_objectives",
        foreign_keys=[controlled_by_id],
    )
    
    def seize(self, player_id: uuid.UUID) -> None:
        """Mark objective as seized by a player."""
        self.status = ObjectiveStatus.SEIZED
        self.controlled_by_id = player_id
    
    def contest(self) -> None:
        """Mark objective as contested."""
        self.status = ObjectiveStatus.CONTESTED
        # Keep controlled_by_id as last controller for reference
    
    def neutralize(self) -> None:
        """Reset objective to neutral."""
        self.status = ObjectiveStatus.NEUTRAL
        self.controlled_by_id = None
    
    @property
    def display_name(self) -> str:
        """Return label if set, otherwise 'Objective X'."""
        return self.label or f"Objective {self.marker_number}"
    
    def __repr__(self) -> str:
        return f"<Objective {self.marker_number}: {self.status.value}>"
