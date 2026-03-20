"""Tests for Army Forge URL / rules parsing (no HTTP)."""

import pytest
from litestar.exceptions import ValidationException

from app.army_forge.parse import extract_list_id, parse_special_rules


def test_extract_list_id_from_share_url():
    assert (
        extract_list_id("https://army-forge.onepagerules.com/share?id=AbCdEf123")
        == "AbCdEf123"
    )


def test_extract_list_id_raw():
    assert extract_list_id("my-list-id-12345") == "my-list-id-12345"


def test_extract_list_id_rejects_console_paste():
    with pytest.raises(ValidationException) as exc:
        extract_list_id("Uncaught TypeError: cannot read property")
    assert "console" in str(exc.value).lower() or "invalid" in str(exc.value).lower()


def test_parse_special_rules_caster_and_transport():
    rules = [
        {"name": "Caster", "rating": 2},
        {"name": "Transport", "rating": 12},
        {"name": "Tough", "rating": 3},
    ]
    out = parse_special_rules(rules)
    assert out["is_caster"] is True
    assert out["caster_level"] == 2
    assert out["is_transport"] is True
    assert out["transport_capacity"] == 12
    assert out["tough"] == 3
