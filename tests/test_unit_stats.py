"""Tests for unit stat parsing and effective caster detection."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.utils.unit_stats import parse_stat_modifications, get_effective_caster


class FakeUnit:
    """Minimal unit-like object for testing get_effective_caster."""

    def __init__(self, *, is_caster=False, caster_level=0, rules=None, loadout=None, upgrades=None):
        self.is_caster = is_caster
        self.caster_level = caster_level
        self.rules = rules or []
        self.loadout = loadout or []
        self.upgrades = upgrades or []


def test_get_effective_caster_from_db_flag():
    """Unit with is_caster=True in DB is detected as caster."""
    unit = FakeUnit(is_caster=True, caster_level=2)
    is_caster, level = get_effective_caster(unit)
    assert is_caster is True
    assert level == 2


def test_get_effective_caster_from_rules():
    """Unit with Caster(X) in rules is detected even if DB flag is False."""
    unit = FakeUnit(
        is_caster=False,
        caster_level=0,
        rules=[{"name": "Caster", "rating": "3"}],
    )
    is_caster, level = get_effective_caster(unit)
    assert is_caster is True
    assert level == 3


def test_get_effective_caster_none():
    """Non-caster unit returns (False, 0)."""
    unit = FakeUnit(is_caster=False, caster_level=0, rules=[{"name": "Tough", "rating": "3"}])
    is_caster, level = get_effective_caster(unit)
    assert is_caster is False
    assert level == 0


def test_get_effective_caster_minimum_level():
    """Caster with level 0 in DB is bumped to level 1."""
    unit = FakeUnit(is_caster=True, caster_level=0)
    is_caster, level = get_effective_caster(unit)
    assert is_caster is True
    assert level == 1


def test_get_effective_caster_rules_override_db():
    """Rules-derived caster level takes precedence when higher than DB value."""
    unit = FakeUnit(
        is_caster=True,
        caster_level=1,
        rules=[{"name": "Caster", "rating": "3"}],
    )
    is_caster, level = get_effective_caster(unit)
    assert is_caster is True
    assert level == 3


def test_parse_stat_modifications_empty():
    """Empty inputs return zero modifications."""
    mods = parse_stat_modifications(rules=[], upgrades=[], loadout=[])
    assert mods["quality"] == 0
    assert mods["defense"] == 0
    assert mods["tough"] is None
    assert mods["caster_level"] is None


def test_parse_stat_modifications_tough_from_rules():
    """Tough(X) rule is extracted correctly."""
    mods = parse_stat_modifications(rules=[{"name": "Tough", "rating": "3"}])
    assert mods["tough"] == 3
