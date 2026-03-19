"""Request/response schemas for game API endpoints."""

import uuid
from datetime import datetime
from typing import Optional, List, Any

from pydantic import BaseModel, Field

from app.models import (
    GameSystem,
    GameStatus,
    DeploymentStatus,
    ObjectiveStatus,
    EventType,
)


class CreateGameRequest(BaseModel):
    """Request to create a new game."""
    name: str = Field(default="New Game", max_length=100)
    game_system: Optional[GameSystem] = Field(default=None)
    player_name: str = Field(max_length=50)
    player_color: str = Field(default="#3b82f6", max_length=20)
    is_solo: bool = Field(default=False, description="Enable solo play mode (single player controls both armies)")
    opponent_name: Optional[str] = Field(default=None, max_length=50, description="Display name for the opponent in solo mode")


class JoinGameRequest(BaseModel):
    """Request to join an existing game."""
    player_name: str = Field(max_length=50)
    player_color: str = Field(default="#ef4444", max_length=20)


class UpdateGameStateRequest(BaseModel):
    """Request to update game state."""
    current_round: Optional[int] = None
    status: Optional[GameStatus] = None
    current_player_id: Optional[uuid.UUID] = None


class UpdateUnitStateRequest(BaseModel):
    """Request to update a unit's state."""
    wounds_taken: Optional[int] = None
    models_remaining: Optional[int] = None
    activated_this_round: Optional[bool] = None
    is_shaken: Optional[bool] = None
    is_fatigued: Optional[bool] = None
    deployment_status: Optional[DeploymentStatus] = None
    transport_id: Optional[uuid.UUID] = None
    spell_tokens: Optional[int] = None
    limited_weapons_used: Optional[List[str]] = None
    custom_notes: Optional[str] = None


class LogUnitActionRequest(BaseModel):
    """Request to log a unit action."""
    action: str = Field(description="Action type: rush, advance, hold, charge, or attack")
    target_unit_ids: Optional[List[uuid.UUID]] = Field(
        default=None,
        description="Target unit IDs (required for charge/attack actions)"
    )


class CastSpellRequest(BaseModel):
    """Request to attempt a spell cast (during activation, before attacks)."""
    spell_value: int = Field(ge=1, le=6, description="Token cost of the spell")
    spell_name: Optional[str] = Field(default=None, max_length=100, description="Spell name for log")
    target_unit_id: Optional[uuid.UUID] = Field(default=None, description="Target unit if applicable")
    roll_modifier: Optional[int] = Field(
        default=None,
        ge=-3,
        le=3,
        description="Modifier to cast roll (e.g. +1 or -1 per allied token spent)",
    )


class UpdateObjectiveRequest(BaseModel):
    """Request to update an objective's state."""
    status: ObjectiveStatus
    controlled_by_id: Optional[uuid.UUID] = None


class CreateObjectivesRequest(BaseModel):
    """Request to create objectives for a game."""
    count: int = Field(ge=3, le=6, default=4)


class UpdateVictoryPointsRequest(BaseModel):
    """Request to update a player's victory points."""
    delta: int = Field(description="Change in VP (+1, -1, etc.)")


class UpdatePlayerNameRequest(BaseModel):
    """Request to update a player's display name (solo mode only)."""
    name: str = Field(..., min_length=1, max_length=50, description="New display name")


class UpdateRoundRequest(BaseModel):
    """Request to update the game round."""
    delta: int = Field(description="Change in round (+1, -1, etc.)")


class CreateUnitRequest(BaseModel):
    """Request to create a unit manually."""
    player_id: uuid.UUID = Field(..., description="Player ID who owns this unit")
    name: str = Field(..., min_length=1, max_length=100, description="Unit name")
    custom_name: Optional[str] = Field(None, max_length=100, description="Custom display name")
    quality: int = Field(4, ge=2, le=6, description="Quality stat (2-6)")
    defense: int = Field(4, ge=2, le=6, description="Defense stat (2-6)")
    size: int = Field(1, ge=1, description="Number of models")
    tough: int = Field(1, ge=1, description="Tough value")
    cost: int = Field(0, ge=0, description="Point cost")
    loadout: Optional[List[Any]] = Field(None, description="Weapon loadout (JSON)")
    rules: Optional[List[Any]] = Field(None, description="Special rules (JSON)")
    upgrades: Optional[List[Any]] = Field(None, description="Upgrades (JSON)")
    is_hero: bool = Field(False, description="Is this a hero unit")
    is_caster: bool = Field(False, description="Is this a caster unit")
    caster_level: int = Field(0, ge=0, le=6, description="Caster level (0-6)")
    is_transport: bool = Field(False, description="Is this a transport unit")
    transport_capacity: int = Field(0, ge=0, description="Transport capacity")
    has_ambush: bool = Field(False, description="Has Ambush rule")
    has_scout: bool = Field(False, description="Has Scout rule")
    attached_to_unit_id: Optional[uuid.UUID] = Field(None, description="Attach to parent unit (for heroes)")


class ClearUnitsResponse(BaseModel):
    """Response after clearing all units."""
    success: bool
    units_cleared: int
    message: str


class SaveGameRequest(BaseModel):
    """Request to save a game state."""
    save_name: str = Field(default="Untitled Save", max_length=100)
    description: Optional[str] = Field(None, max_length=500)


class SaveGameResponse(BaseModel):
    """Response after saving a game."""
    success: bool
    save_id: uuid.UUID
    save_name: str
    message: str


class GameSaveResponse(BaseModel):
    """Response for a game save."""
    id: uuid.UUID
    game_id: uuid.UUID
    save_name: str
    saved_at: datetime
    description: Optional[str]

    class Config:
        from_attributes = True


class LoadGameRequest(BaseModel):
    """Request to load a game state."""
    save_id: uuid.UUID


# --- Response schemas ---

class PlayerResponse(BaseModel):
    """Player data response."""
    id: uuid.UUID
    name: str
    color: str
    is_host: bool
    is_connected: bool
    army_name: Optional[str]
    starting_unit_count: int
    starting_points: int
    victory_points: int
    spells: Optional[List[Any]] = None  # List of {name, cost, description} for casters

    class Config:
        from_attributes = True


class UnitStateResponse(BaseModel):
    """Unit state response."""
    id: uuid.UUID
    wounds_taken: int
    models_remaining: int
    activated_this_round: bool
    is_shaken: bool
    is_fatigued: bool
    deployment_status: DeploymentStatus
    transport_id: Optional[uuid.UUID]
    spell_tokens: int
    limited_weapons_used: Optional[List[str]]
    custom_notes: Optional[str]

    class Config:
        from_attributes = True


class UnitResponse(BaseModel):
    """Unit data response."""
    id: uuid.UUID
    player_id: uuid.UUID
    name: str
    custom_name: Optional[str]
    quality: int
    defense: int
    size: int
    tough: int
    cost: int
    loadout: Optional[List[Any]]
    rules: Optional[List[Any]]
    upgrades: Optional[List[Any]] = None
    is_hero: bool
    is_caster: bool
    caster_level: int
    is_transport: bool
    transport_capacity: int
    has_ambush: bool
    has_scout: bool
    attached_to_unit_id: Optional[uuid.UUID] = None
    state: Optional["UnitStateResponse"]

    class Config:
        from_attributes = True


class ObjectiveResponse(BaseModel):
    """Objective data response."""
    id: uuid.UUID
    marker_number: int
    label: Optional[str]
    status: ObjectiveStatus
    controlled_by_id: Optional[uuid.UUID]

    class Config:
        from_attributes = True


class GameEventResponse(BaseModel):
    """Game event response."""
    id: uuid.UUID
    event_type: EventType
    description: str
    round_number: int
    player_id: Optional[uuid.UUID]
    target_unit_id: Optional[uuid.UUID]
    target_objective_id: Optional[uuid.UUID]
    details: Optional[dict]
    is_undone: bool
    created_at: datetime

    class Config:
        from_attributes = True


class GameResponse(BaseModel):
    """Full game state response."""
    id: uuid.UUID
    code: str
    name: str
    game_system: GameSystem
    status: GameStatus
    is_solo: bool
    current_round: int
    max_rounds: int
    current_player_id: Optional[uuid.UUID]
    first_player_next_round_id: Optional[uuid.UUID]
    players: List[PlayerResponse]
    objectives: List[ObjectiveResponse]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class GameWithUnitsResponse(GameResponse):
    """Game response with full unit data."""
    units: List[UnitResponse] = []


class JoinGameResponse(GameWithUnitsResponse):
    """Response when joining a game - includes your player ID."""
    your_player_id: str = ""
