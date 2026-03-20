"""Branch coverage for parse_stat_modifications and calculate_effective_stats."""

import pytest

from app.utils.unit_stats import calculate_effective_stats, parse_stat_modifications


def test_extract_text_non_dict_non_str():
    assert parse_stat_modifications(rules=[None, 42, {"name": "X"}])["quality"] == 0


def test_absolute_and_additive_defense_patterns():
    mods = parse_stat_modifications(
        rules=[
            {"name": "Armor", "description": "armor(5+)"},
        ]
    )
    assert mods["defense"] == 5
    assert mods["modification_types"]["defense"] == "absolute"

    mods2 = parse_stat_modifications(rules=[{"description": "defence = 4"}])
    assert mods2["defense"] == 4

    mods3 = parse_stat_modifications(rules=[{"text": "+2 defense"}])
    assert mods3["defense"] == 2


def test_tough_and_caster_invalid_rating_fallback_to_text():
    mods = parse_stat_modifications(
        rules=[
            {"name": "tough", "rating": "nope", "description": "tough(4)"},
            {"name": "caster", "rating": {}, "description": "caster level: 2"},
        ]
    )
    assert mods["tough"] == 4
    assert mods["caster_level"] == 2


def test_tough_text_only():
    mods = parse_stat_modifications(rules=[{"description": "Tough = 3"}])
    assert mods["tough"] == 3


def test_quality_and_size_from_text():
    mods = parse_stat_modifications(
        rules=[
            {"description": "+1 quality"},
            {"description": "+2 size"},
        ]
    )
    assert mods["quality"] == 1
    assert mods["size"] == 2


def test_parse_string_rule_extract_only():
    assert parse_stat_modifications(rules=["nonsense text"])["quality"] == 0


def test_upgrade_nested_rules_merge_and_absolute_clash():
    mods = parse_stat_modifications(
        upgrades=[
            {
                "name": "u",
                "description": "armor(4+)",
                "rules": [{"description": "+1 quality"}],
            }
        ]
    )
    assert mods["defense"] == 4
    assert mods["quality"] == 1


def test_upgrade_direct_defense_additive_when_not_absolute():
    mods = parse_stat_modifications(
        upgrades=[{"description": "+1 defense", "rules": []}]
    )
    assert mods["defense"] == 1


def test_upgrade_tough_and_caster_from_nested():
    mods = parse_stat_modifications(
        upgrades=[
            {
                "rules": [
                    {"name": "tough", "rating": "2"},
                    {"name": "caster", "rating": "3"},
                ],
            }
        ]
    )
    assert mods["tough"] == 2
    assert mods["caster_level"] == 3


def test_upgrade_effects_alias():
    mods = parse_stat_modifications(
        upgrades=[{"effects": [{"description": "+1 q"}]}]
    )
    assert mods["quality"] == 1


def test_loadout_additive_defense():
    mods = parse_stat_modifications(loadout=[{"description": "+1 armour"}])
    assert mods["defense"] == 1


def test_empty_rule_skipped():
    assert parse_stat_modifications(rules=[""])["quality"] == 0


def test_rule_logger_paths_absolute_armor():
    parse_stat_modifications(
        rules=[
            {"name": "Named", "description": "armor(3+)"},
            {"description": "+1 defense"},
        ]
    )


def _mt(**over):
    d = {
        "quality": "additive",
        "defense": "additive",
        "tough": "additive",
        "size": "additive",
        "caster_level": "additive",
    }
    d.update(over)
    return d


def test_calculate_effective_quality_absolute():
    out = calculate_effective_stats(
        4,
        4,
        2,
        3,
        1,
        {
            "quality": 6,
            "defense": 0,
            "size": 0,
            "modification_types": _mt(quality="absolute"),
        },
    )
    assert out["effective_quality"] == 6


def test_calculate_effective_defense_absolute_value():
    out = calculate_effective_stats(
        4,
        4,
        2,
        3,
        1,
        {
            "quality": 0,
            "defense": 5,
            "size": 0,
            "modification_types": _mt(defense="absolute"),
        },
    )
    assert out["effective_defense"] == 5


def test_calculate_effective_tough_additive_branch():
    """Tough key omitted: additive uses base + get (0)."""
    out = calculate_effective_stats(
        4,
        4,
        3,
        3,
        1,
        {
            "quality": 0,
            "defense": 0,
            "size": 0,
            "modification_types": _mt(),
        },
    )
    assert out["effective_tough"] == 3


def test_calculate_effective_size_absolute():
    out = calculate_effective_stats(
        4,
        4,
        2,
        3,
        1,
        {
            "quality": 0,
            "defense": 0,
            "size": 5,
            "modification_types": _mt(size="absolute"),
        },
    )
    assert out["effective_size"] == 5


def test_calculate_effective_caster_from_mod():
    out = calculate_effective_stats(
        4,
        4,
        2,
        3,
        0,
        {
            "quality": 0,
            "defense": 0,
            "size": 0,
            "caster_level": 4,
            "modification_types": _mt(),
        },
    )
    assert out["effective_caster_level"] == 4


def test_calculate_effective_clamps():
    out = calculate_effective_stats(
        1,
        1,
        0,
        0,
        10,
        {
            "quality": 0,
            "defense": 0,
            "size": 0,
            "tough": 1,
            "modification_types": _mt(),
        },
    )
    assert out["effective_quality"] == 2
    assert out["effective_defense"] == 2
    assert out["effective_tough"] == 1
    assert out["effective_size"] == 1
    assert out["effective_caster_level"] == 6


def test_parse_dict_rule_and_upgrade_merge_absolute_both():
    mods = parse_stat_modifications(
        rules=[{"description": "armor(3+)"}],
        upgrades=[
            {
                "description": "armor(6+)",
                "rules": [{"description": "+1 quality"}],
            }
        ],
    )
    assert mods["defense"] == 6
    assert mods["quality"] >= 1
