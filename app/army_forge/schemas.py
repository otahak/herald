"""Pydantic models for Army Forge API payloads."""

import uuid
from typing import List, Optional

from pydantic import BaseModel


class ArmyForgeWeapon(BaseModel):
    """Weapon from Army Forge."""

    type: str
    count: int = 1
    label: Optional[str] = None
    name: str
    range: Optional[int] = None
    attacks: Optional[int] = None
    specialRules: Optional[List[dict]] = None
    content: Optional[List["ArmyForgeWeapon"]] = None


class ArmyForgeUnit(BaseModel):
    """Unit from Army Forge API."""

    armyId: str
    name: str
    customName: Optional[str] = None
    id: str
    selectionId: str
    joinToUnit: Optional[str] = None
    combined: bool = False
    defense: int
    quality: int
    size: int
    loadout: List[ArmyForgeWeapon]
    rules: List[dict]
    selectedUpgrades: Optional[List[dict]] = None
    cost: int


class ArmyForgeListResponse(BaseModel):
    """Response from Army Forge TTS API."""

    gameSystem: str
    units: List[ArmyForgeUnit]
    specialRules: Optional[List[dict]] = None


class ImportArmyRequest(BaseModel):
    """Request to import an army from Army Forge."""

    army_forge_url: str
    player_id: uuid.UUID


class ImportArmyResponse(BaseModel):
    """Response after importing an army."""

    units_imported: int
    army_name: str
    total_points: int


ArmyForgeWeapon.model_rebuild()
