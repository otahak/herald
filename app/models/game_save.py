"""Game save model for solo play mode."""

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy import String, ForeignKey, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.game import Game


class GameSave(Base):
    """A saved game state for solo play mode."""
    
    __tablename__ = "game_saves"
    
    # Which game this save belongs to
    game_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"),
        index=True,
    )
    
    # Save metadata
    save_name: Mapped[str] = mapped_column(String(100), default="Untitled Save")
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    
    # Full game state as JSON
    game_state_json: Mapped[str] = mapped_column(Text)
    
    # Optional description
    description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    
    # Relationships
    game: Mapped["Game"] = relationship(
        "Game",
        foreign_keys=[game_id],
    )
    
    def __repr__(self) -> str:
        return f"<GameSave {self.id} for game {self.game_id} - {self.save_name}>"
