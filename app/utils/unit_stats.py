"""Utility functions for parsing unit stat modifications from rules, upgrades, and loadout."""

import re
import logging
from typing import Dict, Optional, List, Any

logger = logging.getLogger("Herald.unit_stats")


def parse_stat_modifications(
    rules: Optional[List[Any]] = None,
    upgrades: Optional[List[Any]] = None,
    loadout: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """
    Parse stat modifications from rules, upgrades, and loadout.
    
    Returns dict with:
    - "quality": modification amount (int, can be negative)
    - "defense": modification amount (int, can be negative) 
    - "tough": absolute value if "Tough(X)" found, or modification amount
    - "size": modification amount (int, can be negative)
    - "caster_level": absolute value if "Caster(X)" found
    - "modification_types": dict indicating if each stat is "absolute" or "additive"
    
    Handles both absolute (overrides) and additive (modifies) changes:
    - "Armor(3+)" sets Defense to 3 (absolute)
    - "+1 Defense" adds 1 to Defense (additive)
    - "Tough(6)" sets Tough to 6 (absolute)
    - "Caster(2)" sets Caster Level to 2 (absolute)
    """
    modifications = {
        "quality": 0,
        "defense": 0,
        "tough": None,  # None means use base, int means override
        "size": 0,
        "caster_level": None,  # None means use base, int means override
    }
    modification_types = {
        "quality": "additive",
        "defense": "additive",
        "tough": "additive",
        "size": "additive",
        "caster_level": "additive",
    }
    
    def extract_text(item: Any) -> str:
        """Extract searchable text from a rule/upgrade/item."""
        if isinstance(item, dict):
            # Combine all text fields
            text_parts = []
            for field in ["name", "description", "label", "text", "title"]:
                if field in item and item[field]:
                    text_parts.append(str(item[field]).lower())
            return " ".join(text_parts)
        elif isinstance(item, str):
            return item.lower()
        return ""
    
    def parse_absolute_defense(text: str) -> Optional[int]:
        """Parse absolute Defense value from 'Armor(X+)' or 'Defense(X+)' patterns."""
        # Match "Armor(3+)" or "Armor 3+" or "Defense(3+)"
        patterns = [
            r'(?:armor|armour|defense|defence)\s*\(?\s*(\d+)\s*\+?\s*\)?',
            r'(?:armor|armour|defense|defence)\s*=\s*(\d+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return int(match.group(1))
        return None
    
    def parse_additive_defense(text: str) -> Optional[int]:
        """Parse additive Defense modification from '+1 Defense' or 'Defense +1' patterns."""
        patterns = [
            r'[+\-]\s*(\d+)\s*(?:defense|defence|armor|armour|d)',
            r'(?:defense|defence|armor|armour|d)\s*[+\-]\s*(\d+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                value = int(match.group(1))
                # Check if it's negative
                if re.search(r'[-\-]', text[:match.start()] + text[match.end():]):
                    return -value
                return value
        return None
    
    def parse_tough(text: str, rating: Any = None) -> Optional[int]:
        """Parse Tough value from 'Tough(X)' pattern or rating field."""
        # First check rating field (common in Army Forge)
        if rating is not None:
            try:
                return int(rating)
            except (ValueError, TypeError):
                pass
        
        # Then check text patterns
        patterns = [
            r'tough\s*\(?\s*(\d+)\s*\)?',
            r'tough\s*=\s*(\d+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return int(match.group(1))
        return None
    
    def parse_caster_level(text: str, rating: Any = None) -> Optional[int]:
        """Parse Caster Level from 'Caster(X)' pattern or rating field."""
        # First check rating field
        if rating is not None:
            try:
                return int(rating)
            except (ValueError, TypeError):
                pass
        
        # Then check text patterns
        patterns = [
            r'caster\s*\(?\s*(\d+)\s*\)?',
            r'caster\s*level\s*[=:]\s*(\d+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return int(match.group(1))
        return None
    
    def parse_additive_stat(stat_name: str, text: str) -> Optional[int]:
        """Parse additive modification for Quality, Size, etc."""
        synonyms = {
            "quality": ["quality", "q"],
            "size": ["size"],
        }
        
        stat_terms = synonyms.get(stat_name, [stat_name])
        patterns = [
            rf'[+\-]\s*(\d+)\s*(?:{"|".join(stat_terms)})',
            rf'(?:{"|".join(stat_terms)})\s*[+\-]\s*(\d+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                value = int(match.group(1))
                # Check if it's negative
                if re.search(r'[-\-]', text[:match.start()] + text[match.end():]):
                    return -value
                return value
        return None
    
    # Check rules
    for rule in (rules or []):
        if not rule:
            continue
            
        text = extract_text(rule)
        rating = rule.get("rating") if isinstance(rule, dict) else None
        
        # Parse absolute Defense (Armor)
        armor_value = parse_absolute_defense(text)
        if armor_value is not None:
            modifications["defense"] = armor_value
            modification_types["defense"] = "absolute"
            logger.debug(f"Found absolute Defense: {armor_value} from rule: {rule.get('name', 'unknown')}")
        
        # Parse additive Defense (if not already absolute)
        if modification_types["defense"] != "absolute":
            defense_mod = parse_additive_defense(text)
            if defense_mod is not None:
                modifications["defense"] += defense_mod
                logger.debug(f"Found additive Defense: {defense_mod} from rule: {rule.get('name', 'unknown')}")
        
        # Parse Tough (absolute)
        tough_value = parse_tough(text, rating)
        if tough_value is not None:
            modifications["tough"] = tough_value
            modification_types["tough"] = "absolute"
            logger.debug(f"Found absolute Tough: {tough_value} from rule: {rule.get('name', 'unknown')}")
        
        # Parse Caster Level (absolute)
        caster_value = parse_caster_level(text, rating)
        if caster_value is not None:
            modifications["caster_level"] = caster_value
            modification_types["caster_level"] = "absolute"
            logger.debug(f"Found absolute Caster Level: {caster_value} from rule: {rule.get('name', 'unknown')}")
        
        # Parse additive Quality
        quality_mod = parse_additive_stat("quality", text)
        if quality_mod is not None:
            modifications["quality"] += quality_mod
            logger.debug(f"Found additive Quality: {quality_mod} from rule: {rule.get('name', 'unknown')}")
        
        # Parse additive Size
        size_mod = parse_additive_stat("size", text)
        if size_mod is not None:
            modifications["size"] += size_mod
            logger.debug(f"Found additive Size: {size_mod} from rule: {rule.get('name', 'unknown')}")
    
    # Check upgrades
    for upgrade in (upgrades or []):
        if not upgrade:
            continue
            
        text = extract_text(upgrade)
        rating = upgrade.get("rating") if isinstance(upgrade, dict) else None
        
        # Check if upgrade grants rules that modify stats
        # Some upgrades might have a "rules" or "effects" field
        upgrade_rules = upgrade.get("rules") or upgrade.get("effects") or []
        if upgrade_rules:
            # Recursively parse rules from upgrade
            upgrade_mods = parse_stat_modifications(rules=upgrade_rules)
            # Merge modifications (upgrades can override)
            for stat in ["quality", "defense", "size"]:
                if upgrade_mods[stat] != 0:
                    if modification_types[stat] == "absolute" and upgrade_mods.get("modification_types", {}).get(stat) == "absolute":
                        modifications[stat] = upgrade_mods[stat]
                    else:
                        modifications[stat] += upgrade_mods[stat]
            
            if upgrade_mods["tough"] is not None:
                modifications["tough"] = upgrade_mods["tough"]
                modification_types["tough"] = "absolute"
            
            if upgrade_mods["caster_level"] is not None:
                modifications["caster_level"] = upgrade_mods["caster_level"]
                modification_types["caster_level"] = "absolute"
        
        # Also check upgrade text directly
        armor_value = parse_absolute_defense(text)
        if armor_value is not None:
            modifications["defense"] = armor_value
            modification_types["defense"] = "absolute"
        
        defense_mod = parse_additive_defense(text)
        if defense_mod is not None and modification_types["defense"] != "absolute":
            modifications["defense"] += defense_mod
    
    # Check loadout (weapons might have stat modifications)
    for item in (loadout or []):
        if not item:
            continue
            
        text = extract_text(item)
        
        # Weapons typically don't modify unit stats, but check anyway
        # Some special weapons might grant stat bonuses
        defense_mod = parse_additive_defense(text)
        if defense_mod is not None and modification_types["defense"] != "absolute":
            modifications["defense"] += defense_mod
    
    modifications["modification_types"] = modification_types
    return modifications


def calculate_effective_stats(
    base_quality: int,
    base_defense: int,
    base_tough: int,
    base_size: int,
    base_caster_level: int,
    modifications: Dict[str, Any],
) -> Dict[str, int]:
    """
    Calculate effective stats from base stats and modifications.
    
    Returns dict with effective_quality, effective_defense, effective_tough, effective_size, effective_caster_level.
    """
    mod_types = modifications.get("modification_types", {})
    
    # Quality (additive)
    if mod_types.get("quality") == "absolute":
        effective_quality = modifications.get("quality", base_quality)
    else:
        effective_quality = base_quality + modifications.get("quality", 0)
    
    # Defense (can be absolute or additive)
    if mod_types.get("defense") == "absolute":
        effective_defense = modifications.get("defense", base_defense)
    else:
        effective_defense = base_defense + modifications.get("defense", 0)
    
    # Tough (typically absolute from "Tough(X)" rule)
    if modifications.get("tough") is not None:
        effective_tough = modifications["tough"]
    else:
        effective_tough = base_tough + modifications.get("tough", 0) if mod_types.get("tough") == "additive" else base_tough
    
    # Size (additive)
    if mod_types.get("size") == "absolute":
        effective_size = modifications.get("size", base_size)
    else:
        effective_size = base_size + modifications.get("size", 0)
    
    # Caster Level (typically absolute from "Caster(X)" rule)
    if modifications.get("caster_level") is not None:
        effective_caster_level = modifications["caster_level"]
    else:
        effective_caster_level = base_caster_level
    
    return {
        "effective_quality": max(2, min(6, effective_quality)),  # Clamp to valid range
        "effective_defense": max(2, min(6, effective_defense)),  # Clamp to valid range
        "effective_tough": max(1, effective_tough),  # Clamp to minimum 1
        "effective_size": max(1, effective_size),  # Clamp to minimum 1
        "effective_caster_level": max(0, min(6, effective_caster_level)),  # Clamp to valid range
    }
