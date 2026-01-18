"""Game event model for action logging."""

import enum
import uuid
from typing import TYPE_CHECKING, Optional, Any, Dict

from sqlalchemy import String, Integer, ForeignKey, Enum, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.game import Game
    from app.models.player import Player


class EventType(str, enum.Enum):
    """Types of game events that get logged."""
    
    # Game flow events
    GAME_STARTED = "game_started"
    GAME_ENDED = "game_ended"
    ROUND_STARTED = "round_started"
    ROUND_ENDED = "round_ended"
    TURN_CHANGED = "turn_changed"
    
    # Unit events
    UNIT_ACTIVATED = "unit_activated"
    UNIT_WOUNDED = "unit_wounded"
    UNIT_HEALED = "unit_healed"
    UNIT_DESTROYED = "unit_destroyed"
    UNIT_DEPLOYED = "unit_deployed"        # From ambush/reserve
    UNIT_EMBARKED = "unit_embarked"        # Entered transport
    UNIT_DISEMBARKED = "unit_disembarked"  # Left transport
    UNIT_DETACHED = "unit_detached"        # Hero detached from parent unit
    # Unit action events
    UNIT_RUSHED = "unit_rushed"
    UNIT_ADVANCED = "unit_advanced"
    UNIT_HELD = "unit_held"
    UNIT_CHARGED = "unit_charged"
    UNIT_ATTACKED = "unit_attacked"
    
    # Status events
    STATUS_SHAKEN = "status_shaken"
    STATUS_SHAKEN_CLEARED = "status_shaken_cleared"
    STATUS_FATIGUED = "status_fatigued"
    
    # Resource events
    SPELL_CAST = "spell_cast"
    SPELL_TOKENS_GAINED = "spell_tokens_gained"
    SPELL_TOKENS_SPENT = "spell_tokens_spent"
    LIMITED_WEAPON_USED = "limited_weapon_used"
    
    # Objective events
    OBJECTIVE_SEIZED = "objective_seized"
    OBJECTIVE_CONTESTED = "objective_contested"
    OBJECTIVE_NEUTRALIZED = "objective_neutralized"
    
    # Player events
    PLAYER_JOINED = "player_joined"
    PLAYER_LEFT = "player_left"
    ARMY_IMPORTED = "army_imported"
    VP_CHANGED = "vp_changed"  # Victory points changed
    
    # Other
    CUSTOM = "custom"
    UNDO = "undo"  # When an action is undone


class GameEvent(Base):
    """
    A logged game event for history and undo support.
    
    Every state change creates an event with:
    - Human-readable description
    - Machine-readable details (JSON)
    - References to affected entities
    - Game state context (round, turn)
    """
    
    __tablename__ = "game_events"
    
    # Which game this event belongs to
    game_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"),
        index=True,
    )
    
    # Who triggered the event (null for system events)
    player_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("players.id", ondelete="SET NULL"),
        nullable=True,
    )
    
    # Event type - use native enum (SQLAlchemy stores enum NAMES by default, matching database)
    event_type: Mapped[EventType] = mapped_column(Enum(EventType, native_enum=True))
    
    # Human-readable description
    # e.g., "Player 1 dealt 2 wounds to Tactical Squad (4/6 remaining)"
    description: Mapped[str] = mapped_column(Text)
    
    # Game state at time of event
    round_number: Mapped[int] = mapped_column(Integer, default=1)
    
    # Target references (optional, depends on event type)
    target_unit_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("units.id", ondelete="SET NULL"),
        nullable=True,
    )
    target_objective_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("objectives.id", ondelete="SET NULL"),
        nullable=True,
    )
    
    # Detailed event data (JSON)
    # Structure depends on event_type, examples:
    # UNIT_WOUNDED: {"wounds": 2, "wounds_before": 2, "wounds_after": 4}
    # SPELL_CAST: {"spell_name": "Mind War", "tokens_spent": 2, "success": true}
    # OBJECTIVE_SEIZED: {"objective_number": 2, "previous_status": "neutral"}
    details: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    
    # For undo support: snapshot of state before this event
    # Allows reverting to previous state
    previous_state: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    
    # Has this event been undone?
    is_undone: Mapped[bool] = mapped_column(default=False)
    
    # Relationships
    game: Mapped["Game"] = relationship("Game", back_populates="events")
    
    @classmethod
    def create(
        cls,
        game_id: uuid.UUID,
        event_type: EventType,
        description: str,
        player_id: Optional[uuid.UUID] = None,
        round_number: int = 1,
        target_unit_id: Optional[uuid.UUID] = None,
        target_objective_id: Optional[uuid.UUID] = None,
        details: Optional[Dict[str, Any]] = None,
        previous_state: Optional[Dict[str, Any]] = None,
    ) -> "GameEvent":
        """Factory method to create a new event."""
        return cls(
            game_id=game_id,
            player_id=player_id,
            event_type=event_type,  # EventType is str enum, so this is already the value string
            description=description,
            round_number=round_number,
            target_unit_id=target_unit_id,
            target_objective_id=target_objective_id,
            details=details,
            previous_state=previous_state,
        )
    
    def __repr__(self) -> str:
        return f"<GameEvent R{self.round_number} {self.event_type.value}: {self.description[:50]}>"
