"""Player model."""

import uuid
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import String, Integer, Boolean, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.game import Game
    from app.models.unit import Unit
    from app.models.objective import Objective


class Player(Base):
    """A player in a game session."""
    
    __tablename__ = "players"
    
    # Which game this player belongs to
    game_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"),
        index=True,
    )
    
    # Player identity
    name: Mapped[str] = mapped_column(String(50))
    color: Mapped[str] = mapped_column(String(20), default="#3b82f6")  # Tailwind blue-500
    
    # Army Forge integration (optional)
    army_forge_list_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    army_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    
    # Game role
    is_host: Mapped[bool] = mapped_column(Boolean, default=False)
    is_connected: Mapped[bool] = mapped_column(Boolean, default=True)
    
    # Army tracking for morale calculations
    starting_unit_count: Mapped[int] = mapped_column(Integer, default=0)
    starting_points: Mapped[int] = mapped_column(Integer, default=0)
    
    # Turn tracking
    has_finished_activations: Mapped[bool] = mapped_column(Boolean, default=False)
    
    # Relationships
    game: Mapped["Game"] = relationship(
        "Game",
        back_populates="players",
        foreign_keys=[game_id],
    )
    units: Mapped[List["Unit"]] = relationship(
        "Unit",
        back_populates="player",
        cascade="all, delete-orphan",
    )
    controlled_objectives: Mapped[List["Objective"]] = relationship(
        "Objective",
        back_populates="controlled_by_player",
        foreign_keys="Objective.controlled_by_id",
    )
    
    @property
    def current_unit_count(self) -> int:
        """Count of non-destroyed units."""
        return sum(1 for u in self.units if not u.state or not u.state.is_destroyed)
    
    @property
    def morale_threshold_reached(self) -> bool:
        """True if army is at half or less of starting units."""
        if self.starting_unit_count == 0:
            return False
        return self.current_unit_count <= (self.starting_unit_count // 2)
    
    @property
    def army_health_percentage(self) -> float:
        """Percentage of army remaining (0.0 to 1.0)."""
        if self.starting_unit_count == 0:
            return 1.0
        return self.current_unit_count / self.starting_unit_count
    
    def __repr__(self) -> str:
        return f"<Player {self.name} in game {self.game_id}>"
