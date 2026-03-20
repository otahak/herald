"""Parse Army Forge URLs, rules, loadouts, and share API payloads."""

import re
from typing import List, Optional

from litestar.exceptions import ValidationException


def extract_list_id(url_or_id: str) -> str:
    """Extract list ID from Army Forge share URL or raw ID."""
    if not url_or_id or not isinstance(url_or_id, str):
        raise ValidationException("Invalid Army Forge URL or ID provided")

    url_or_id = url_or_id.strip()

    if any(
        indicator in url_or_id.lower()
        for indicator in [
            "vue.global.js",
            "console",
            "error",
            "warn",
            "traceback",
            "exception",
            "uncaught",
            "typeerror",
            "cannot read",
            "property",
            "undefined",
            "null",
        ]
    ):
        raise ValidationException(
            "Invalid input detected. Please paste the Army Forge share URL or list ID, not console output. "
            "Example: https://army-forge.onepagerules.com/share?id=XXXXX"
        )

    if not url_or_id.startswith("http"):
        if re.match(r"^[a-zA-Z0-9_-]{5,50}$", url_or_id):
            return url_or_id
        raise ValidationException(
            f"Invalid list ID format. Expected alphanumeric characters, dashes, or underscores. "
            f"Got: {url_or_id[:50]}..."
        )

    match = re.search(r"(?:id=|share/)([a-zA-Z0-9_-]+)", url_or_id)
    if match:
        list_id = match.group(1)
        if len(list_id) < 5 or len(list_id) > 50:
            raise ValidationException(f"Extracted list ID has invalid length: {len(list_id)} characters")
        return list_id

    raise ValidationException(
        f"Could not extract list ID from the provided input. "
        f"Please provide either:\n"
        f"- A full Army Forge share URL (e.g., https://army-forge.onepagerules.com/share?id=XXXXX)\n"
        f"- Or just the list ID (alphanumeric string)"
    )


def weapon_to_loadout_item(w: dict) -> dict:
    """Convert army book weapon/item to TTS-style loadout entry."""
    sr = w.get("specialRules") or []
    rules = []
    for r in sr:
        if isinstance(r, dict):
            rules.append({"name": r.get("name", r.get("label", "")), "rating": r.get("rating")})
    return {
        "name": w.get("name", ""),
        "label": w.get("label"),
        "range": w.get("range"),
        "attacks": w.get("attacks"),
        "specialRules": rules if rules else None,
    }


def upgrade_options_by_uid(book: dict) -> dict[str, dict]:
    """Map Army Forge upgrade option uid/id -> option object (from upgradePackages)."""
    out: dict[str, dict] = {}
    for pkg in book.get("upgradePackages") or []:
        if not isinstance(pkg, dict):
            continue
        for sec in pkg.get("sections") or []:
            if not isinstance(sec, dict):
                continue
            for opt in sec.get("options") or []:
                if not isinstance(opt, dict):
                    continue
                for key in (opt.get("uid"), opt.get("id")):
                    if key:
                        out[str(key)] = opt
    return out


def enrich_share_upgrades_for_display(selected: list, book: dict | None) -> list:
    """Turn raw {optionId, upgradeId, instanceId} rows into objects with label for the UI."""
    if not selected:
        return []
    if not book:
        return list(selected)
    by_uid = upgrade_options_by_uid(book)
    out: list = []
    for su in selected:
        if not isinstance(su, dict):
            continue
        oid = su.get("optionId")
        opt = by_uid.get(str(oid)) if oid else None
        label = (opt.get("label") or "").strip() if opt else ""
        if label:
            out.append(
                {
                    "label": label,
                    "optionId": su.get("optionId"),
                    "upgradeId": su.get("upgradeId"),
                    "instanceId": su.get("instanceId"),
                }
            )
        else:
            out.append(su)
    return out


def merge_campaign_traits_into_rules(rules: list, share_unit: dict) -> None:
    """Append narrative/campaign traits from share API as rule badges."""
    existing = {
        ((r.get("name") or r.get("label") or "").strip().lower())
        for r in rules
        if isinstance(r, dict)
    }
    for t in share_unit.get("traits") or []:
        if not isinstance(t, str):
            continue
        name = t.strip()
        if not name:
            continue
        low = name.lower()
        if low in existing:
            continue
        rules.append({"name": name, "rating": None})
        existing.add(low)


def apply_share_upgrades_from_book(
    selected: list,
    book: dict | None,
    rules: list,
    loadout: list,
    base_defense: int,
) -> int:
    """
    Apply upgrade-option gains (rules, weapons, Armor) from the army book.
    Returns defense value after applying Armor(X) from upgrade content, if any.
    """
    defense = base_defense
    if not book or not selected:
        return defense
    by_uid = upgrade_options_by_uid(book)
    seen_rules = {
        (
            str((r.get("name") or r.get("label") or "")).strip().lower(),
            r.get("rating"),
        )
        for r in rules
        if isinstance(r, dict)
    }

    for su in selected:
        if not isinstance(su, dict):
            continue
        oid = su.get("optionId")
        if not oid:
            continue
        opt = by_uid.get(str(oid))
        if not opt:
            continue
        for gain in opt.get("gains") or []:
            if not isinstance(gain, dict):
                continue
            gtype = gain.get("type") or ""
            if gtype == "ArmyBookWeapon" or (gain.get("range") is not None and gain.get("attacks") is not None):
                w = {
                    "name": gain.get("name") or gain.get("label") or "Weapon",
                    "label": gain.get("label"),
                    "range": gain.get("range"),
                    "attacks": gain.get("attacks"),
                    "specialRules": gain.get("specialRules"),
                }
                loadout.append(weapon_to_loadout_item(w))
            for item in gain.get("content") or []:
                if not isinstance(item, dict):
                    continue
                name = (item.get("name") or item.get("label") or "").strip()
                if not name:
                    continue
                rating = item.get("rating")
                low = name.lower()
                key = (low, rating)
                if key in seen_rules:
                    continue
                rules.append({"name": name, "rating": rating})
                seen_rules.add(key)
                if low == "armor" and rating is not None:
                    try:
                        defense = int(rating)
                    except (ValueError, TypeError):
                        pass
    return defense


def share_notes(share_unit: dict) -> Optional[str]:
    n = share_unit.get("notes")
    if isinstance(n, str):
        n = n.strip()
        return n or None
    return None


def share_unit_to_tts_placeholder(
    share_unit: dict,
    army_book: Optional[dict] = None,
) -> dict:
    """Minimal TTS-style unit when army book is unavailable."""
    name = share_unit.get("customName") or "Unit (army book unavailable)"
    rules = []
    merge_campaign_traits_into_rules(rules, share_unit)
    loadout = []
    selected = share_unit.get("selectedUpgrades") or []
    defense = apply_share_upgrades_from_book(selected, army_book, rules, loadout, 4)
    enriched = enrich_share_upgrades_for_display(selected, army_book)
    return {
        "id": share_unit.get("id"),
        "selectionId": share_unit.get("selectionId", share_unit.get("id")),
        "armyId": share_unit.get("armyId"),
        "name": name,
        "customName": None,
        "quality": 4,
        "defense": defense,
        "size": 1,
        "cost": 0,
        "loadout": loadout,
        "rules": rules,
        "selectedUpgrades": enriched,
        "joinToUnit": share_unit.get("joinToUnit"),
        "combined": share_unit.get("combined", False),
        "notes": share_notes(share_unit),
    }


def share_unit_to_tts(
    share_unit: dict,
    book_unit: dict | None,
    army_book: Optional[dict] = None,
) -> dict:
    """Convert share API unit + army book unit to TTS-style unit dict."""
    if book_unit is None:
        return share_unit_to_tts_placeholder(share_unit, army_book)
    loadout = []
    for w in book_unit.get("weapons") or []:
        loadout.append(weapon_to_loadout_item(w))
    for item in book_unit.get("items") or []:
        loadout.append(weapon_to_loadout_item(item))
    rules = []
    for r in book_unit.get("rules") or []:
        if isinstance(r, dict):
            rules.append({"name": r.get("name", r.get("label", "")), "rating": r.get("rating")})
    merge_campaign_traits_into_rules(rules, share_unit)

    selected = share_unit.get("selectedUpgrades") or []
    try:
        base_defense = int(book_unit.get("defense", 4))
    except (ValueError, TypeError):
        base_defense = 4
    defense = apply_share_upgrades_from_book(selected, army_book, rules, loadout, base_defense)
    enriched = enrich_share_upgrades_for_display(selected, army_book)

    return {
        "id": share_unit.get("id"),
        "selectionId": share_unit.get("selectionId", share_unit.get("id")),
        "armyId": share_unit.get("armyId"),
        "name": book_unit.get("name", "Unknown Unit"),
        "customName": share_unit.get("customName"),
        "quality": book_unit.get("quality", 4),
        "defense": defense,
        "size": book_unit.get("size", 1),
        "cost": book_unit.get("cost", 0),
        "loadout": loadout,
        "rules": rules,
        "selectedUpgrades": enriched,
        "joinToUnit": share_unit.get("joinToUnit"),
        "combined": share_unit.get("combined", False),
        "notes": share_notes(share_unit),
    }


def parse_special_rules(rules: List[dict]) -> dict:
    """Parse special rules to extract key unit properties."""
    result = {
        "is_hero": False,
        "is_caster": False,
        "caster_level": 0,
        "is_transport": False,
        "transport_capacity": 0,
        "has_ambush": False,
        "has_scout": False,
        "tough": 1,
    }

    for rule in rules:
        name = rule.get("name", "").lower()
        rating = rule.get("rating")

        if name == "hero":
            result["is_hero"] = True
        elif name == "caster":
            result["is_caster"] = True
            result["caster_level"] = int(rating) if rating else 1
        elif name == "transport":
            result["is_transport"] = True
            result["transport_capacity"] = int(rating) if rating else 6
        elif name == "ambush":
            result["has_ambush"] = True
        elif name == "scout":
            result["has_scout"] = True
        elif name == "tough":
            result["tough"] = int(rating) if rating else 1

    return result


def caster_level_from_loadout_item(item: dict) -> int:
    """Extract caster level from a loadout item's rating field, name, or label."""
    if item.get("rating") is not None:
        try:
            return int(item["rating"])
        except (ValueError, TypeError):
            pass
    for field in ("name", "label"):
        text = (item.get(field) or "").strip()
        m = re.search(r"caster\s*\(\s*(\d+)\s*\)", text, re.I)
        if m:
            return int(m.group(1))
    return 1


def is_flavor_caster_name(name: str) -> bool:
    """True if name is a flavor title with (Caster(N))."""
    if not name or not name.strip():
        return False
    n = name.strip().lower()
    return "caster" in n and bool(re.search(r"caster\s*\(\s*\d+\s*\)", n))


def parse_loadout_for_caster(loadout: list) -> tuple:
    """
    Treat Caster as a skill like Hero, Tough(X), etc.
    Returns (is_caster, caster_level, loadout_cleaned, rules_to_add).
    """
    if not loadout or not isinstance(loadout, list):
        return False, 0, loadout or [], []
    is_caster_ref = [False]
    caster_level_ref = [0]
    rules_to_add: list = []

    def walk(items: list) -> list:
        out = []
        for item in (i for i in items if isinstance(i, dict)):
            name = (item.get("name") or item.get("label") or "").strip()
            name_lower = name.lower()
            if name_lower == "caster" or re.match(r"^caster\s*\(\s*\d+\s*\)\s*$", name_lower):
                is_caster_ref[0] = True
                caster_level_ref[0] = max(caster_level_ref[0], caster_level_from_loadout_item(item))
                continue
            if is_flavor_caster_name(name):
                is_caster_ref[0] = True
                caster_level_ref[0] = max(caster_level_ref[0], caster_level_from_loadout_item(item))
                rules_to_add.append({"name": name, "rating": None})
                continue
            special_rules = item.get("specialRules")
            if special_rules:
                cleaned = []
                for r in special_rules:
                    if isinstance(r, dict) and (r.get("name") or r.get("label") or "").strip().lower() == "caster":
                        is_caster_ref[0] = True
                        caster_level_ref[0] = max(
                            caster_level_ref[0], int(r.get("rating")) if r.get("rating") is not None else 1
                        )
                    else:
                        cleaned.append(r)
                if len(cleaned) != len(special_rules):
                    item = {**item, "specialRules": cleaned}
            content = item.get("content")
            if content and isinstance(content, list):
                item = {**item, "content": walk(content)}
            out.append(item)
        return out

    filtered = walk(loadout)
    return is_caster_ref[0], caster_level_ref[0], filtered, rules_to_add


def parse_upgrades_for_caster(upgrades: list) -> tuple:
    """Returns (is_caster, caster_level)."""
    if not upgrades or not isinstance(upgrades, list):
        return False, 0
    is_caster = False
    caster_level = 0

    def check_item(item: dict) -> None:
        nonlocal is_caster, caster_level
        if not isinstance(item, dict):
            return
        name = (item.get("name") or item.get("label") or "").strip()
        name_lower = name.lower()
        if name_lower == "caster" or re.match(r"^caster\s*\(\s*\d+\s*\)\s*$", name_lower):
            is_caster = True
            caster_level = max(caster_level, caster_level_from_loadout_item(item))
        elif is_flavor_caster_name(name):
            is_caster = True
            caster_level = max(caster_level, caster_level_from_loadout_item({"name": name, "label": name}))
        for key in ("rules", "effects", "options", "choices", "content"):
            for sub in item.get(key) or []:
                if isinstance(sub, dict):
                    n = (sub.get("name") or sub.get("label") or "").strip().lower()
                    if n == "caster":
                        is_caster = True
                        caster_level = max(caster_level, int(sub.get("rating")) if sub.get("rating") is not None else 1)
                    check_item(sub)

    for up in upgrades:
        check_item(up)
    return is_caster, caster_level
