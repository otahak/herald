"""Shared helpers for game API route handlers."""

from app.api.game_schemas import UnitResponse
from app.models import Unit
from app.utils.unit_stats import get_effective_caster


def unit_response_with_effective_caster(unit: Unit) -> UnitResponse:
    """Build UnitResponse with is_caster/caster_level from DB or from rules/loadout/upgrades."""
    resp = UnitResponse.model_validate(unit)
    effective_caster, effective_level = get_effective_caster(unit)
    resp.is_caster = effective_caster
    if effective_caster:
        resp.caster_level = effective_level or resp.caster_level or 1
    return resp
