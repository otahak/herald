"""Unit and UnitState models."""

import enum
import uuid
from typing import TYPE_CHECKING, Optional, List, Any

from sqlalchemy import String, Integer, Boolean, ForeignKey, Enum, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.player import Player


class DeploymentStatus(str, enum.Enum):
    """Unit deployment state."""
    DEPLOYED = "deployed"       # On the battlefield
    IN_AMBUSH = "in_ambush"     # Waiting to deploy (Ambush rule)
    IN_RESERVE = "in_reserve"   # In reserve (various rules)
    EMBARKED = "embarked"       # Inside a transport
    DESTROYED = "destroyed"     # Removed from play


class Unit(Base):
    """
    A unit in a player's army.
    
    Stores the base unit profile data, either imported from Army Forge
    or entered manually. The actual game state (wounds, activations, etc.)
    is tracked in the related UnitState.
    """
    
    __tablename__ = "units"
    
    # Which player owns this unit
    player_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"),
        index=True,
    )
    
    # Unit identity
    name: Mapped[str] = mapped_column(String(100))
    custom_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    
    # Core stats
    quality: Mapped[int] = mapped_column(Integer, default=4)  # e.g., 4 for 4+
    defense: Mapped[int] = mapped_column(Integer, default=4)  # e.g., 4 for 4+
    
    # Model count
    size: Mapped[int] = mapped_column(Integer, default=1)  # Starting model count
    tough: Mapped[int] = mapped_column(Integer, default=1)  # Tough(X) value, default 1
    
    # Points cost
    cost: Mapped[int] = mapped_column(Integer, default=0)
    
    # Army Forge data (JSON blobs for flexibility)
    loadout: Mapped[Optional[List[Any]]] = mapped_column(JSON, nullable=True)
    rules: Mapped[Optional[List[Any]]] = mapped_column(JSON, nullable=True)
    upgrades: Mapped[Optional[List[Any]]] = mapped_column(JSON, nullable=True)
    
    # Army Forge identifiers (for reference)
    army_forge_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    army_forge_selection_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    
    # Special unit flags (derived from rules, cached for quick access)
    is_hero: Mapped[bool] = mapped_column(Boolean, default=False)
    is_caster: Mapped[bool] = mapped_column(Boolean, default=False)
    caster_level: Mapped[int] = mapped_column(Integer, default=0)  # Caster(X) value
    is_transport: Mapped[bool] = mapped_column(Boolean, default=False)
    transport_capacity: Mapped[int] = mapped_column(Integer, default=0)
    has_ambush: Mapped[bool] = mapped_column(Boolean, default=False)
    has_scout: Mapped[bool] = mapped_column(Boolean, default=False)
    
    # Unit attachment (for heroes attached to other units)
    attached_to_unit_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("units.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    
    # Relationships
    player: Mapped["Player"] = relationship("Player", back_populates="units")
    attached_to_unit: Mapped[Optional["Unit"]] = relationship(
        "Unit",
        remote_side="Unit.id",
        foreign_keys=[attached_to_unit_id],
        back_populates="attached_heroes",
    )
    attached_heroes: Mapped[List["Unit"]] = relationship(
        "Unit",
        foreign_keys=[attached_to_unit_id],
        back_populates="attached_to_unit",
    )
    state: Mapped[Optional["UnitState"]] = relationship(
        "UnitState",
        back_populates="unit",
        uselist=False,
        cascade="all, delete-orphan",
        foreign_keys="UnitState.unit_id",
    )
    
    # Transport relationships (units inside this transport)
    embarked_units: Mapped[List["UnitState"]] = relationship(
        "UnitState",
        back_populates="transport",
        foreign_keys="UnitState.transport_id",
    )
    
    @property
    def display_name(self) -> str:
        """Return custom name if set, otherwise base name."""
        return self.custom_name or self.name
    
    @property
    def max_wounds(self) -> int:
        """Maximum wounds this unit can take before destruction."""
        # For multi-model units: size models, each with tough wounds
        # For single tough models: just the tough value
        return self.size * self.tough
    
    def __repr__(self) -> str:
        return f"<Unit {self.display_name} [{self.size}] Q{self.quality}+ D{self.defense}+>"


class UnitState(Base):
    """
    Per-game state tracking for a unit.
    
    Separated from Unit to keep base profile data clean and allow
    easy state resets.
    """
    
    __tablename__ = "unit_states"
    
    # Which unit this state belongs to
    unit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("units.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    
    # Damage tracking
    wounds_taken: Mapped[int] = mapped_column(Integer, default=0)
    models_remaining: Mapped[int] = mapped_column(Integer, default=0)  # Set from unit.size on init
    
    # Activation tracking (reset each round)
    activated_this_round: Mapped[bool] = mapped_column(Boolean, default=False)
    
    # Status effects
    is_shaken: Mapped[bool] = mapped_column(Boolean, default=False)
    is_fatigued: Mapped[bool] = mapped_column(Boolean, default=False)
    
    # Deployment status
    deployment_status: Mapped[DeploymentStatus] = mapped_column(
        Enum(DeploymentStatus),
        default=DeploymentStatus.DEPLOYED,
    )
    
    # Transport tracking (if embarked in a transport)
    transport_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("units.id", ondelete="SET NULL"),
        nullable=True,
    )
    
    # Spell tokens for Caster units (0-6)
    spell_tokens: Mapped[int] = mapped_column(Integer, default=0)
    
    # Limited weapons usage (list of weapon names that have been used)
    limited_weapons_used: Mapped[Optional[List[str]]] = mapped_column(JSON, nullable=True)
    
    # Player notes
    custom_notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    
    # Relationships
    unit: Mapped["Unit"] = relationship(
        "Unit",
        back_populates="state",
        foreign_keys=[unit_id],
    )
    transport: Mapped[Optional["Unit"]] = relationship(
        "Unit",
        back_populates="embarked_units",
        foreign_keys=[transport_id],
    )
    
    @property
    def is_destroyed(self) -> bool:
        """True if unit has been destroyed."""
        return self.deployment_status == DeploymentStatus.DESTROYED
    
    @property
    def wounds_remaining(self) -> int:
        """Calculate remaining wounds from unit's max."""
        if not self.unit:
            return 0
        return self.unit.max_wounds - self.wounds_taken
    
    @property
    def health_percentage(self) -> float:
        """Unit health as percentage (0.0 to 1.0)."""
        if not self.unit or self.unit.max_wounds == 0:
            return 1.0
        return max(0, self.wounds_remaining / self.unit.max_wounds)
    
    def reset_for_new_round(self) -> None:
        """Reset per-round state (called at round start)."""
        self.activated_this_round = False
        self.is_fatigued = False
        # Shaken persists until cleared by spending activation
    
    def __repr__(self) -> str:
        unit_name = self.unit.display_name if self.unit else "Unknown"
        return f"<UnitState {unit_name}: {self.wounds_taken} wounds, {self.deployment_status.value}>"
