"""Army Forge API proxy endpoints."""

import logging
import re
import uuid
from typing import Optional, List, Any

import httpx
from litestar import Controller, get, post
from litestar.exceptions import NotFoundException, ValidationException
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
                        f"Army Forge server error - the list may be invalid, expired, or Army Forge may be experiencing issues. "
                        f"Try re-sharing your list from Army Forge."
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
                    except:
                        error_text = e.response.text[:200] if e.response.text else "No error details"
                        error_detail = error_text
                    
                    logger.error(f"Army Forge 500 error details: {error_detail}")
                    raise ValidationException(
                        f"Army Forge server error (500) for list '{list_id}'. "
                        f"This may happen with custom armies or expired lists. "
                        f"Please try:\n"
                        f"1. Re-sharing the list from Army Forge\n"
                        f"2. Verifying the list ID is correct\n"
                        f"3. Using a different army list\n\n"
                        f"Error details: {error_detail[:100]}"
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
        unit_data_with_attachments: list[tuple[dict, Unit]] = []
        
        for unit_data in units_data:
            try:
                # Parse special rules
                rules = unit_data.get("rules", [])
                props = parse_special_rules(rules)
                
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
                    loadout=unit_data.get("loadout"),
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
                    unit_data_with_attachments.append((unit_data, unit))
                
                # Create initial state
                initial_deployment = (
                    DeploymentStatus.IN_AMBUSH if props["has_ambush"]
                    else DeploymentStatus.DEPLOYED
                )
                
                state = UnitState(
                    unit_id=unit.id,
                    models_remaining=unit.size,
                    spell_tokens=props["caster_level"] if props["is_caster"] else 0,
                    deployment_status=initial_deployment,
                )
                session.add(state)
                
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
        
        # Second pass: Link attached heroes to their parent units
        for unit_data, attached_unit in unit_data_with_attachments:
            join_to_selection_id = unit_data.get("joinToUnit")
            if join_to_selection_id and join_to_selection_id in selection_id_to_unit:
                parent_unit = selection_id_to_unit[join_to_selection_id]
                attached_unit.attached_to_unit_id = parent_unit.id
                logger.debug(f"Linked {attached_unit.name} to {parent_unit.name}")
            else:
                logger.warning(f"Could not find parent unit with selectionId '{join_to_selection_id}' for attached unit '{attached_unit.name}'")
        
        # Store values for response before any commits
        player_name = player.name
        player_id = player.id
        game_id = game.id
        game_code = game.code  # cache code to avoid lazy load after commit
        current_round = game.current_round
        
        # Update player stats (accumulate instead of replace)
        player.army_forge_list_id = list_id
        army_name = f"Imported Army ({units_created} units)"
        player.army_name = army_name
        # Accumulate units and points instead of replacing
        player.starting_unit_count = (player.starting_unit_count or 0) + units_created
        player.starting_points = (player.starting_points or 0) + total_points
        
        # Log the import - create event directly to avoid relationship access
        event = GameEvent(
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
