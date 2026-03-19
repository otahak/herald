"""Army Forge API proxy endpoints."""

import logging
import re
import uuid
from typing import Optional, List, Any

import httpx
from litestar import Controller, get, post
from litestar.exceptions import NotFoundException, ValidationException, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    Game, Player, Unit, UnitState, DeploymentStatus,
    GameEvent, EventType,
)
from app.api.websocket import broadcast_to_game
from app.utils.logging import error_log, log_exception_with_context

logger = logging.getLogger("Herald.proxy")


# --- Army Forge Response Types ---

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
    army_forge_url: str  # Full share URL or just the ID
    player_id: uuid.UUID


class ImportArmyResponse(BaseModel):
    """Response after importing an army."""
    units_imported: int
    army_name: str
    total_points: int


# --- Helper Functions ---

def extract_list_id(url_or_id: str) -> str:
    """Extract list ID from Army Forge share URL or raw ID."""
    if not url_or_id or not isinstance(url_or_id, str):
        raise ValidationException("Invalid Army Forge URL or ID provided")
    
    # Clean up the input - remove any whitespace
    url_or_id = url_or_id.strip()
    
    # Check if it looks like console output or error text (common patterns)
    if any(indicator in url_or_id.lower() for indicator in [
        'vue.global.js',
        'console',
        'error',
        'warn',
        'traceback',
        'exception',
        'uncaught',
        'typeerror',
        'cannot read',
        'property',
        'undefined',
        'null',
    ]):
        raise ValidationException(
            "Invalid input detected. Please paste the Army Forge share URL or list ID, not console output. "
            "Example: https://army-forge.onepagerules.com/share?id=XXXXX"
        )
    
    # If it's already just an ID (alphanumeric with dashes/underscores, reasonable length)
    if not url_or_id.startswith("http"):
        # Validate it looks like a reasonable ID (alphanumeric, dashes, underscores, 5-50 chars)
        if re.match(r'^[a-zA-Z0-9_-]{5,50}$', url_or_id):
            return url_or_id
        else:
            raise ValidationException(
                f"Invalid list ID format. Expected alphanumeric characters, dashes, or underscores. "
                f"Got: {url_or_id[:50]}..."
            )
    
    # Try to extract from URL
    # Format: https://army-forge.onepagerules.com/share?id=XXXXX
    # Or: https://army-forge.onepagerules.com/listbuilder/share/XXXXX
    match = re.search(r'(?:id=|share/)([a-zA-Z0-9_-]+)', url_or_id)
    if match:
        list_id = match.group(1)
        # Validate the extracted ID
        if len(list_id) < 5 or len(list_id) > 50:
            raise ValidationException(f"Extracted list ID has invalid length: {len(list_id)} characters")
        return list_id
    
    raise ValidationException(
        f"Could not extract list ID from the provided input. "
        f"Please provide either:\n"
        f"- A full Army Forge share URL (e.g., https://army-forge.onepagerules.com/share?id=XXXXX)\n"
        f"- Or just the list ID (alphanumeric string)"
    )


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


def _caster_level_from_loadout_item(item: dict) -> int:
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


def _is_flavor_caster_name(name: str) -> bool:
    """True if name is a flavor title with (Caster(N)), e.g. 'Technomancer (Caster(2))'."""
    if not name or not name.strip():
        return False
    n = name.strip().lower()
    return "caster" in n and bool(re.search(r"caster\s*\(\s*\d+\s*\)", n))


def parse_loadout_for_caster(loadout: list) -> tuple:
    """
    Treat Caster as a skill like Hero, Tough(X), etc.
    Returns (is_caster, caster_level, loadout_cleaned, rules_to_add).
    - Purely "Caster" / "Caster(N)" items: remove from loadout.
    - Flavor items like "Technomancer (Caster(2))": remove from loadout and add to rules_to_add
      so they display in the Rules section as "Technomancer (Caster(2))".
    - specialRules Caster on other items: strip from that item, set is_caster.
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
            # Purely caster-only item: remove from loadout
            if name_lower == "caster" or re.match(r"^caster\s*\(\s*\d+\s*\)\s*$", name_lower):
                is_caster_ref[0] = True
                caster_level_ref[0] = max(caster_level_ref[0], _caster_level_from_loadout_item(item))
                continue
            # Flavor title with (Caster(N)): move to Rules, remove from loadout
            if _is_flavor_caster_name(name):
                is_caster_ref[0] = True
                caster_level_ref[0] = max(caster_level_ref[0], _caster_level_from_loadout_item(item))
                rules_to_add.append({"name": name, "rating": None})  # display as "Technomancer (Caster(2))"
                continue
            # Strip Caster from specialRules so equipment doesn't show [Caster(2)]
            special_rules = item.get("specialRules")
            if special_rules:
                cleaned = []
                for r in special_rules:
                    if isinstance(r, dict) and (r.get("name") or r.get("label") or "").strip().lower() == "caster":
                        is_caster_ref[0] = True
                        caster_level_ref[0] = max(caster_level_ref[0], int(r.get("rating")) if r.get("rating") is not None else 1)
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
    """
    Check selectedUpgrades for Caster (e.g. upgrade name "Caster(2)" or upgrade granting Caster rule).
    Returns (is_caster, caster_level).
    """
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
            caster_level = max(caster_level, _caster_level_from_loadout_item(item))
        elif _is_flavor_caster_name(name):
            is_caster = True
            caster_level = max(caster_level, _caster_level_from_loadout_item({"name": name, "label": name}))
        for key in ("rules", "effects", "options", "choices", "content"):
            for sub in (item.get(key) or []):
                if isinstance(sub, dict):
                    n = (sub.get("name") or sub.get("label") or "").strip().lower()
                    if n == "caster":
                        is_caster = True
                        caster_level = max(caster_level, int(sub.get("rating")) if sub.get("rating") is not None else 1)
                    check_item(sub)

    for up in upgrades:
        check_item(up)
    return is_caster, caster_level


async def log_event(
    session: AsyncSession,
    game: Game,
    event_type: EventType,
    description: str,
    player_id: Optional[uuid.UUID] = None,
    details: Optional[dict] = None,
) -> GameEvent:
    """Create and persist a game event."""
    event = GameEvent.create(
        game_id=game.id,
        event_type=event_type,
        description=description,
        player_id=player_id,
        round_number=game.current_round,
        details=details,
    )
    session.add(event)
    return event


# --- Controller ---

class ProxyController(Controller):
    """Proxy endpoints for external API integration."""
    
    path = "/api/proxy"
    tags = ["proxy"]
    
    @get("/army-forge/{list_id:str}")
    async def get_army_forge_list(self, list_id: str) -> ArmyForgeListResponse:
        """
        Fetch an army list from Army Forge.
        
        This proxies the request to avoid CORS issues and allows
        the frontend to fetch lists directly.
        """
        url = f"https://army-forge.onepagerules.com/api/tts?id={list_id}"
        logger.info(f"Fetching Army Forge list: {list_id}")
        logger.debug(f"Army Forge URL: {url}")
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(url, timeout=15.0)
                logger.debug(f"Army Forge response status: {response.status_code}")
                response.raise_for_status()
                data = response.json()
                logger.info(f"Successfully fetched list {list_id}: {len(data.get('units', []))} units")
                return ArmyForgeListResponse(**data)
            except httpx.HTTPStatusError as e:
                error_log(
                    "Army Forge HTTP error",
                    exc=e,
                    context={
                        "list_id": list_id,
                        "status_code": e.response.status_code,
                        "response_preview": e.response.text[:200] if e.response.text else "empty",
                    }
                )
                if e.response.status_code == 404:
                    raise NotFoundException(f"Army list '{list_id}' not found on Army Forge")
                elif e.response.status_code == 500:
                    raise ValidationException(
                        "Army Forge's export API doesn't support this list (e.g. combined armies like The Ashen Pact, "
                        "or custom/community books). Use a single-army official list or add units manually."
                    )
                raise ValidationException(f"Army Forge API error: {e.response.status_code}")
            except httpx.TimeoutException as e:
                error_log(
                    "Timeout fetching Army Forge list",
                    exc=e,
                    context={"list_id": list_id}
                )
                raise ValidationException("Army Forge request timed out. Please try again.")
            except httpx.RequestError as e:
                error_log(
                    "Request error fetching Army Forge list",
                    exc=e,
                    context={"list_id": list_id}
                )
                raise ValidationException(f"Failed to connect to Army Forge: {str(e)}")
    
    @post("/import-army/{game_code:str}")
    async def import_army(
        self,
        game_code: str,
        data: ImportArmyRequest,
        session: AsyncSession,
    ) -> ImportArmyResponse:
        """
        Import an army from Army Forge into a game.

        This fetches the army list, creates Unit and UnitState records
        for each unit, and links them to the specified player.
        """
        from app.utils.rate_limit import check_rate_limit
        if not check_rate_limit(f"import_army:{game_code.upper()}", max_requests=10, window_sec=60):
            raise HTTPException(status_code=429, detail="Too many import requests. Please try again in a minute.")
        logger.info(f"Import army request for game {game_code}, player {data.player_id}")
        logger.debug(f"Army Forge URL/ID: {data.army_forge_url}")
        
        # Get the game
        stmt = (
            select(Game)
            .where(Game.code == game_code.upper())
            .options(selectinload(Game.players))
        )
        result = await session.execute(stmt)
        game = result.scalar_one_or_none()
        
        if not game:
            logger.warning(f"Game not found: {game_code}")
            raise NotFoundException(f"Game with code '{game_code}' not found")
        
        # Find the player
        player = None
        for p in game.players:
            if p.id == data.player_id:
                player = p
                break
        
        if not player:
            logger.warning(f"Player {data.player_id} not found in game {game_code}")
            raise NotFoundException(f"Player {data.player_id} not found in game")
        
        # Update activity tracking
        from datetime import datetime, timezone
        game.last_activity_at = datetime.now(timezone.utc)
        
        # Extract list ID and fetch from Army Forge
        try:
            list_id = extract_list_id(data.army_forge_url)
            logger.debug(f"Extracted list ID: {list_id}")
        except ValidationException as e:
            logger.error(f"Failed to extract list ID from: {data.army_forge_url}")
            raise
        
        async with httpx.AsyncClient() as client:
            try:
                logger.info(f"Fetching Army Forge list: {list_id}")
                response = await client.get(
                    f"https://army-forge.onepagerules.com/api/tts?id={list_id}",
                    timeout=15.0
                )
                response.raise_for_status()
                army_data = response.json()
                logger.debug(f"Army data received: {len(army_data.get('units', []))} units")
            except httpx.HTTPStatusError as e:
                logger.error(f"Army Forge HTTP error: {e.response.status_code}")
                if e.response.status_code == 404:
                    raise NotFoundException(f"Army list '{list_id}' not found on Army Forge")
                elif e.response.status_code == 500:
                    # Try to get more details from the response
                    error_detail = "Unknown error"
                    try:
                        error_body = e.response.json()
                        error_detail = error_body.get("error", error_body.get("message", str(error_body)))
                    except Exception:
                        error_text = e.response.text[:200] if e.response.text else "No error details"
                        error_detail = error_text
                    
                    logger.error(f"Army Forge 500 error details: {error_detail}")
                    raise ValidationException(
                        "Army Forge's export API returned an error for this list. "
                        "This often affects combined-army lists (e.g. The Ashen Pact), custom/community army books, "
                        "or lists Army Forge's TTS export doesn't support—a limitation on Army Forge's side. "
                        "Workaround: add units manually, or try a single-army list from an official book. "
                        "If you believe this is an official list that should work, please report it to One Page Rules."
                    )
                raise ValidationException(f"Army Forge API error: {e.response.status_code}")
            except httpx.TimeoutException:
                logger.error(f"Timeout fetching Army Forge list {list_id}")
                raise ValidationException("Army Forge request timed out. Please try again.")
            except httpx.RequestError as e:
                logger.error(f"Request error: {str(e)}")
                raise ValidationException(f"Failed to connect to Army Forge: {str(e)}")
        
        # Parse units
        units_data = army_data.get("units", [])
        total_points = 0
        units_created = 0
        
        logger.info(f"Processing {len(units_data)} units from Army Forge")
        
        # First pass: Create all units and build a mapping of selectionId -> unit
        selection_id_to_unit: dict[str, Unit] = {}
        unit_id_to_state: dict[uuid.UUID, UnitState] = {}
        unit_data_with_attachments: list[tuple[dict, Unit]] = []
        unit_data_combined: list[tuple[dict, Unit]] = []
        
        for unit_data in units_data:
            try:
                # Parse special rules (from rules array)
                rules = unit_data.get("rules", [])
                props = parse_special_rules(rules)
                # Caster can appear in loadout in Army Forge; treat as skill and move to Rules
                loadout_raw = unit_data.get("loadout", [])
                loadout_is_caster, loadout_caster_level, loadout_filtered, rules_from_loadout = parse_loadout_for_caster(loadout_raw)
                upgrades_raw = unit_data.get("selectedUpgrades") or []
                upgrade_is_caster, upgrade_caster_level = parse_upgrades_for_caster(upgrades_raw)
                if loadout_is_caster or upgrade_is_caster:
                    props["is_caster"] = True
                    props["caster_level"] = max(
                        props["caster_level"] or 0,
                        loadout_caster_level or 0,
                        upgrade_caster_level or 0,
                        1,
                    )
                rules = rules + rules_from_loadout
                
                unit_name = unit_data.get("name", "Unknown Unit")
                logger.debug(f"Creating unit: {unit_name} (Q{unit_data.get('quality', 4)}+ D{unit_data.get('defense', 4)}+)")
                
                # Create unit
                unit = Unit(
                    player_id=player.id,
                    name=unit_name,
                    custom_name=unit_data.get("customName"),
                    quality=unit_data.get("quality", 4),
                    defense=unit_data.get("defense", 4),
                    size=unit_data.get("size", 1),
                    tough=props["tough"],
                    cost=unit_data.get("cost", 0),
                    loadout=loadout_filtered,
                    rules=rules,
                    upgrades=unit_data.get("selectedUpgrades"),
                    army_forge_id=unit_data.get("id"),
                    army_forge_selection_id=unit_data.get("selectionId"),
                    is_hero=props["is_hero"],
                    is_caster=props["is_caster"],
                    caster_level=props["caster_level"],
                    is_transport=props["is_transport"],
                    transport_capacity=props["transport_capacity"],
                    has_ambush=props["has_ambush"],
                    has_scout=props["has_scout"],
                )
                session.add(unit)
                await session.flush()  # Get unit ID
                
                # Store mapping for attachment linking
                selection_id = unit_data.get("selectionId")
                if selection_id:
                    selection_id_to_unit[selection_id] = unit
                
                # Store units with joinToUnit for second pass
                if unit_data.get("joinToUnit"):
                    if unit_data.get("combined") and not props["is_hero"]:
                        unit_data_combined.append((unit_data, unit))
                    else:
                        unit_data_with_attachments.append((unit_data, unit))
                
                # Create initial state
                initial_deployment = (
                    DeploymentStatus.IN_AMBUSH if props["has_ambush"]
                    else DeploymentStatus.DEPLOYED
                )
                
                unit_notes = unit_data.get("notes")
                if isinstance(unit_notes, str):
                    unit_notes = unit_notes.strip() or None
                else:
                    unit_notes = None

                state = UnitState(
                    unit_id=unit.id,
                    models_remaining=unit.size,
                    spell_tokens=props["caster_level"] if props["is_caster"] else 0,
                    deployment_status=initial_deployment,
                    custom_notes=unit_notes,
                )
                session.add(state)
                unit_id_to_state[unit.id] = state
                
                total_points += unit.cost
            except Exception as e:
                error_log(
                    "Error creating unit during Army Forge import",
                    exc=e,
                    context={
                        "unit_name": unit_data.get('name', 'Unknown'),
                        "game_code": game_code,
                        "player_id": str(data.player_id) if hasattr(data, 'player_id') else None,
                    }
                )
                raise ValidationException(f"Failed to import unit '{unit_data.get('name', 'Unknown')}': {str(e)}")
            units_created += 1
        
        # Second pass A: Merge combined units into their parent (doubled squad size)
        for unit_data, combined_unit in unit_data_combined:
            join_to_selection_id = unit_data.get("joinToUnit")
            if join_to_selection_id and join_to_selection_id in selection_id_to_unit:
                parent_unit = selection_id_to_unit[join_to_selection_id]
                parent_unit.size += combined_unit.size
                parent_unit.cost += combined_unit.cost
                parent_state = unit_id_to_state.get(parent_unit.id)
                if parent_state:
                    parent_state.models_remaining = parent_unit.size
                combined_state = unit_id_to_state.pop(combined_unit.id, None)
                if combined_state:
                    await session.delete(combined_state)
                await session.delete(combined_unit)
                units_created -= 1
                logger.debug(
                    f"Merged combined unit {combined_unit.name} into {parent_unit.name} "
                    f"(new size: {parent_unit.size})"
                )
            else:
                logger.warning(
                    f"Could not find parent unit with selectionId '{join_to_selection_id}' "
                    f"for combined unit '{combined_unit.name}' — keeping as separate unit"
                )

        # Second pass B: Link attached heroes to their parent units
        for unit_data, attached_unit in unit_data_with_attachments:
            join_to_selection_id = unit_data.get("joinToUnit")
            if join_to_selection_id and join_to_selection_id in selection_id_to_unit:
                parent_unit = selection_id_to_unit[join_to_selection_id]
                attached_unit.attached_to_unit_id = parent_unit.id
                logger.debug(f"Linked hero {attached_unit.name} to {parent_unit.name}")
            else:
                logger.warning(
                    f"Could not find parent unit with selectionId '{join_to_selection_id}' "
                    f"for attached unit '{attached_unit.name}'"
                )
        
        # Use Army Forge list total when available (includes upgrades); unit cost is base-only
        list_points = army_data.get("listPoints")
        if list_points is not None and isinstance(list_points, (int, float)) and list_points >= 0:
            total_points = int(list_points)
            logger.debug(f"Using listPoints from API: {total_points}")
        # else: total_points remains sum of unit costs (fallback for older or non-standard payloads)
        
        # Store values for response before any commits
        player_name = player.name
        player_id = player.id
        game_id = game.id
        game_code = game.code  # cache code to avoid lazy load after commit
        current_round = game.current_round
        
        # Update player stats (accumulate instead of replace)
        player.army_forge_list_id = list_id

        # Fetch army book for spells, faction rules, and metadata
        army_id = None
        game_system = army_data.get("gameSystem")
        for u in units_data:
            if isinstance(u, dict) and u.get("armyId"):
                army_id = u["armyId"]
                break

        army_book: dict = {}
        if army_id and game_system:
            try:
                async with httpx.AsyncClient() as book_client:
                    book_resp = await book_client.get(
                        f"https://army-forge.onepagerules.com/api/army-books/{army_id}?gameSystem={game_system}",
                        timeout=10.0,
                    )
                    book_resp.raise_for_status()
                    army_book = book_resp.json()
                    logger.info(
                        f"Fetched army book '{army_book.get('name', '?')}' "
                        f"v{army_book.get('versionString', '?')}: "
                        f"{len(army_book.get('spells', []))} spells, "
                        f"{len(army_book.get('specialRules', []))} rules"
                    )
            except Exception as e:
                logger.warning(f"Could not fetch army book for {army_id}: {e}")

        # Army identity (merge with existing if re-importing)
        faction = army_book.get("factionName") or army_book.get("name")
        if faction:
            if player.faction_name and player.faction_name != faction:
                army_name = f"{player.faction_name} + {faction}"
                player.faction_name = army_name
            else:
                army_name = faction
                player.faction_name = faction
        else:
            army_name = player.army_name or f"Imported Army ({units_created} units)"
        player.army_name = army_name
        player.army_book_version = army_book.get("versionString") or player.army_book_version

        # Spells (merge with existing, deduplicate by name)
        existing_spells = player.spells or []
        existing_spell_names = {s["name"] for s in existing_spells if isinstance(s, dict)}
        spells_raw = army_book.get("spells") or army_data.get("spells") or []
        if isinstance(spells_raw, list) and spells_raw:
            parsed_spells = list(existing_spells)
            for s in spells_raw:
                if not isinstance(s, dict):
                    continue
                spell_name = s.get("name", "")
                if spell_name in existing_spell_names:
                    continue
                threshold = s.get("threshold")
                if threshold is not None:
                    try:
                        token_cost = int(threshold)
                    except (ValueError, TypeError):
                        token_cost = 1
                    casting_roll = token_cost + 3
                else:
                    try:
                        token_cost = int(s.get("cost", s.get("value", 1)))
                    except (ValueError, TypeError):
                        token_cost = 1
                    casting_roll = token_cost + 3
                parsed_spells.append({
                    "name": spell_name,
                    "cost": token_cost,
                    "casting_roll": casting_roll,
                    "description": s.get("effect", s.get("description", s.get("text", ""))),
                })
            player.spells = parsed_spells or None

        # Faction special rules (merge with existing, deduplicate by name)
        existing_rules = player.special_rules or []
        existing_rule_names = {r["name"] for r in existing_rules if isinstance(r, dict)}
        rules_raw = army_book.get("specialRules") or []
        if isinstance(rules_raw, list) and rules_raw:
            parsed_rules = list(existing_rules)
            for r in rules_raw:
                if not isinstance(r, dict) or not r.get("name"):
                    continue
                if r["name"] in existing_rule_names:
                    continue
                parsed_rules.append({
                    "name": r["name"],
                    "description": r.get("description", ""),
                    "hasRating": r.get("hasRating", False),
                })
            player.special_rules = parsed_rules or None

        # Accumulate units and points (supports multi-list imports)
        player.starting_unit_count = (player.starting_unit_count or 0) + units_created
        player.starting_points = (player.starting_points or 0) + total_points
        
        # Log the import (use .create() for consistency; can't use log_event()
        # because the game object may be stale after earlier flushes)
        event = GameEvent.create(
            game_id=game_id,
            player_id=player_id,
            event_type=EventType.ARMY_IMPORTED,
            description=f"{player_name} imported army: {units_created} units, {total_points}pts",
            round_number=current_round,
            details={
                "list_id": list_id,
                "units_count": units_created,
                "total_points": total_points,
            },
        )
        session.add(event)
        
        logger.info(f"Import complete: {units_created} units, {total_points}pts for player {player_name}")
        
        await session.commit()
        
        # Broadcast to connected clients to refresh state
        await broadcast_to_game(game_code, {
            "type": "state_update",
            "data": {"reason": "army_imported", "player_id": str(player_id)},
        })
        
        return ImportArmyResponse(
            units_imported=units_created,
            army_name=army_name,
            total_points=total_points,
        )
