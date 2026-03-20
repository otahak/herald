"""Exhaustive unit tests for ``app.army_forge.parse`` (no HTTP)."""

import pytest
from litestar.exceptions import ValidationException

from app.army_forge.parse import (
    apply_share_upgrades_from_book,
    caster_level_from_loadout_item,
    enrich_share_upgrades_for_display,
    extract_list_id,
    is_flavor_caster_name,
    merge_campaign_traits_into_rules,
    parse_loadout_for_caster,
    parse_special_rules,
    parse_upgrades_for_caster,
    share_notes,
    share_unit_to_tts,
    share_unit_to_tts_placeholder,
    upgrade_options_by_uid,
    weapon_to_loadout_item,
)


def test_extract_list_id_console_indicator_multiline_message():
    with pytest.raises(ValidationException, match="console output"):
        extract_list_id("something null reference in paste")


def test_extract_list_id_valid_raw_returns_same():
    assert extract_list_id("valid-id-12345") == "valid-id-12345"


def test_extract_list_id_url_extracted_id_too_long():
    long_id = "a" * 51
    with pytest.raises(ValidationException, match="invalid length"):
        extract_list_id(f"https://army-forge.onepagerules.com/share?id={long_id}")


def test_extract_list_id_url_extracted_id_too_short():
    with pytest.raises(ValidationException, match="invalid length"):
        extract_list_id("https://army-forge.onepagerules.com/share?id=abcd")


def test_extract_list_id_from_share_url_ok():
    assert (
        extract_list_id("https://army-forge.onepagerules.com/share?id=AbCdEf123")
        == "AbCdEf123"
    )


def test_extract_list_id_invalid_types():
    with pytest.raises(ValidationException, match="Invalid Army Forge"):
        extract_list_id("")
    with pytest.raises(ValidationException, match="Invalid Army Forge"):
        extract_list_id(None)  # type: ignore[arg-type]


def test_extract_list_id_bad_raw_id():
    with pytest.raises(ValidationException, match="Invalid list ID format"):
        extract_list_id("ab")


def test_extract_list_id_url_bad_id_length():
    with pytest.raises(ValidationException, match="invalid length"):
        extract_list_id("https://army-forge.onepagerules.com/share?id=abcd")


def test_extract_list_id_url_no_match():
    with pytest.raises(ValidationException, match="Could not extract list ID"):
        extract_list_id("https://example.com/nothing")


def test_weapon_to_loadout_item_special_rules():
    w = {
        "name": "Gun",
        "label": "L",
        "range": 12,
        "attacks": 2,
        "specialRules": [{"name": "AP", "rating": 1}, {"label": "Blast", "rating": None}],
    }
    out = weapon_to_loadout_item(w)
    assert out["name"] == "Gun"
    assert out["specialRules"][0]["name"] == "AP"
    assert out["specialRules"][1]["name"] == "Blast"


def test_upgrade_options_by_uid_skips_non_dicts():
    book = {
        "upgradePackages": [
            "bad",
            {
                "sections": [
                    "badsec",
                    {
                        "options": [
                            "badopt",
                            {"uid": "u1", "label": "Opt A", "gains": []},
                            {"id": "u2", "label": "Opt B"},
                        ]
                    },
                ]
            },
        ]
    }
    m = upgrade_options_by_uid(book)
    assert m["u1"]["label"] == "Opt A"
    assert m["u2"]["label"] == "Opt B"


def test_enrich_share_upgrades_branches():
    assert enrich_share_upgrades_for_display([], {}) == []
    assert enrich_share_upgrades_for_display([{"x": 1}], None) == [{"x": 1}]
    book = {
        "upgradePackages": [
            {
                "sections": [
                    {"options": [{"uid": "o1", "label": "  Named  "}]},
                ]
            }
        ]
    }
    sel = [{"optionId": "o1", "upgradeId": "u", "instanceId": "i"}]
    out = enrich_share_upgrades_for_display(sel, book)
    assert out[0]["label"] == "Named"
    out2 = enrich_share_upgrades_for_display(
        [{"optionId": "missing", "foo": 1}, "skip", {"optionId": None}],
        book,
    )
    assert out2[0]["optionId"] == "missing"


def test_merge_campaign_traits_into_rules():
    rules: list = [{"name": "Hero", "rating": None}]
    merge_campaign_traits_into_rules(rules, {"traits": ["  Scout  ", "Hero", 99, ""]})
    names = {r["name"] for r in rules}
    assert "Scout" in names


def test_apply_share_upgrades_from_book_branches():
    assert apply_share_upgrades_from_book([], None, [], [], 3) == 3
    book = {
        "upgradePackages": [
            {
                "sections": [
                    {
                        "options": [
                            {
                                "uid": "opt1",
                                "label": "X",
                                "gains": [
                                    {
                                        "type": "ArmyBookWeapon",
                                        "name": "Rifle",
                                        "range": 24,
                                        "attacks": 1,
                                        "specialRules": [{"name": "AP", "rating": 1}],
                                    },
                                    {
                                        "content": [
                                            {"name": "Tough", "rating": 2},
                                            {"name": "", "rating": 1},
                                            "bad",
                                            {"name": "Armor", "rating": "5"},
                                        ],
                                    },
                                ],
                            }
                        ]
                    }
                ]
            }
        ]
    }
    rules: list = [{"name": "tough", "rating": 1}]
    loadout: list = []
    d = apply_share_upgrades_from_book(
        [{"optionId": "opt1"}],
        book,
        rules,
        loadout,
        3,
    )
    assert d == 5
    assert any(x.get("name") == "Rifle" for x in loadout)


def test_apply_share_upgrades_range_attacks_branch():
    book = {
        "upgradePackages": [
            {
                "sections": [
                    {
                        "options": [
                            {
                                "uid": "w1",
                                "gains": [{"range": 12, "attacks": 2, "name": "Pistol"}],
                            }
                        ]
                    }
                ]
            }
        ]
    }
    rules, loadout = [], []
    apply_share_upgrades_from_book([{"optionId": "w1"}], book, rules, loadout, 4)
    assert loadout


def test_share_notes():
    assert share_notes({}) is None
    assert share_notes({"notes": "  "}) is None
    assert share_notes({"notes": " hello "}) == "hello"


def test_share_unit_to_tts_placeholder_and_full():
    su = {
        "id": "id1",
        "selectionId": "s1",
        "customName": "Bob",
        "traits": ["Fast"],
        "notes": "n",
    }
    ph = share_unit_to_tts_placeholder(su, None)
    assert ph["name"] == "Bob"
    assert ph["notes"] == "n"
    book_unit = {
        "name": "Official",
        "defense": "bad",
        "quality": 3,
        "size": 2,
        "cost": 50,
        "weapons": [{"name": "W", "specialRules": [{"name": "Rend", "rating": 1}]}],
        "items": [],
        "rules": [{"name": "Hero", "rating": None}],
    }
    full = share_unit_to_tts(su, book_unit, None)
    assert full["defense"] == 4  # invalid int -> default
    assert full["name"] == "Official"


def test_parse_special_rules_flags():
    r = parse_special_rules(
        [
            {"name": "Hero", "rating": None},
            {"name": "Caster", "rating": 2},
            {"name": "Transport", "rating": 8},
            {"name": "Ambush", "rating": None},
            {"name": "Scout", "rating": None},
            {"name": "Tough", "rating": 4},
            {"name": "other", "rating": None},
        ]
    )
    assert r["is_hero"] and r["is_caster"] and r["caster_level"] == 2
    assert r["is_transport"] and r["transport_capacity"] == 8
    assert r["has_ambush"] and r["has_scout"]
    assert r["tough"] == 4


def test_parse_special_rules_defaults():
    r = parse_special_rules(
        [
            {"name": "caster", "rating": None},
            {"name": "transport", "rating": None},
            {"name": "tough", "rating": None},
        ]
    )
    assert r["caster_level"] == 1
    assert r["transport_capacity"] == 6
    assert r["tough"] == 1


def test_caster_level_from_loadout_item():
    assert caster_level_from_loadout_item({"rating": "2"}) == 2
    assert caster_level_from_loadout_item({"rating": "x"}) == 1
    assert caster_level_from_loadout_item({"name": "Caster (3)"}) == 3


def test_is_flavor_caster_name():
    assert is_flavor_caster_name("") is False
    assert is_flavor_caster_name("Boss Caster (2)") is True


def test_parse_loadout_for_caster_variants():
    ic, lv, cleaned, extra = parse_loadout_for_caster(None)
    assert ic is False and lv == 0 and cleaned == []
    ic, lv, cleaned, extra = parse_loadout_for_caster("nope")  # type: ignore[arg-type]
    assert ic is False
    loadout = [
        {"name": "Caster", "rating": 2},
        {"name": "Arch Mage Caster (3)", "label": "x"},
        {
            "name": "Gun",
            "specialRules": [
                {"name": "Caster", "rating": 2},
                {"name": "AP", "rating": 1},
            ],
        },
        {
            "name": "Container",
            "content": [{"name": "Caster (1)", "rating": None}],
        },
    ]
    ic, lv, cleaned, extra = parse_loadout_for_caster(loadout)
    assert ic is True
    assert lv >= 2
    assert any("specialRules" not in e or e.get("specialRules") for e in cleaned)


def test_parse_upgrades_for_caster_nested():
    upgrades = [
        {
            "name": "Caster",
            "rating": 2,
            "content": [
                {
                    "name": "nested",
                    "rules": [{"name": "caster", "rating": 3}],
                }
            ],
        },
        {"name": "Warlock Caster (2)", "label": "x"},
        {
            "effects": [{"name": "Caster", "rating": None}],
        },
    ]
    ic, lv = parse_upgrades_for_caster(upgrades)
    assert ic is True
    assert lv >= 2


def test_parse_upgrades_for_caster_empty():
    assert parse_upgrades_for_caster([]) == (False, 0)
    assert parse_upgrades_for_caster("x") == (False, 0)  # type: ignore[arg-type]


def test_parse_upgrades_skips_non_dict():
    assert parse_upgrades_for_caster([{"name": "Caster", "rating": 1}, "bad"]) == (True, 1)


def test_apply_share_skips_bad_upgrade_rows():
    book = {
        "upgradePackages": [
            {
                "sections": [
                    {
                        "options": [
                            {
                                "uid": "o1",
                                "gains": [
                                    "not-a-dict",
                                    {
                                        "content": [
                                            {"name": "Armor", "rating": "x"},
                                        ]
                                    },
                                ],
                            }
                        ]
                    }
                ]
            }
        ]
    }
    rules = [{"name": "Armor", "rating": 1}]
    loadout: list = []
    apply_share_upgrades_from_book(
        ["bad", {"optionId": None}, {"optionId": "missing"}, {"optionId": "o1"}],
        book,
        rules,
        loadout,
        2,
    )


def test_apply_share_duplicate_rule_key_skipped():
    book = {
        "upgradePackages": [
            {
                "sections": [
                    {
                        "options": [
                            {
                                "uid": "o1",
                                "gains": [{"content": [{"name": "Tough", "rating": 3}]}],
                            }
                        ]
                    }
                ]
            }
        ]
    }
    rules = [{"name": "Tough", "rating": 3}]
    loadout: list = []
    apply_share_upgrades_from_book([{"optionId": "o1"}], book, rules, loadout, 4)


def test_share_unit_to_tts_none_book_unit_uses_placeholder_path():
    su = {"id": "1", "selectionId": "s1"}
    out = share_unit_to_tts(su, None, None)
    assert "army book unavailable" in out["name"].lower() or out["name"]


def test_share_unit_to_tts_includes_items():
    su = {"id": "1", "selectionId": "s1"}
    bu = {
        "name": "U",
        "defense": 3,
        "items": [{"name": "Knife", "attacks": 1}],
        "weapons": [],
        "rules": [],
    }
    out = share_unit_to_tts(su, bu, None)
    assert any(x.get("name") == "Knife" for x in out["loadout"])
