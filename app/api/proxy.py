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
    # If it's already just an ID
    if not url_or_id.startswith("http"):
        return url_or_id
    
    # Try to extract from URL
    # Format: https://army-forge.onepagerules.com/share?id=XXXXX
    # Or: https://army-forge.onepagerules.com/listbuilder/share/XXXXX
    match = re.search(r'(?:id=|share/)([a-zA-Z0-9_-]+)', url_or_id)
    if match:
        return match.group(1)
    
    raise ValidationException(f"Could not extract list ID from: {url_or_id}")


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
                logger.error(f"Army Forge HTTP error for list {list_id}: {e.response.status_code}")
                logger.debug(f"Response body: {e.response.text[:500] if e.response.text else 'empty'}")
                if e.response.status_code == 404:
                    raise NotFoundException(f"Army list '{list_id}' not found on Army Forge")
                elif e.response.status_code == 500:
                    raise ValidationException(
                        f"Army Forge server error - the list may be invalid, expired, or Army Forge may be experiencing issues. "
                        f"Try re-sharing your list from Army Forge."
                    )
                raise ValidationException(f"Army Forge API error: {e.response.status_code}")
            except httpx.TimeoutException:
                logger.error(f"Timeout fetching Army Forge list {list_id}")
                raise ValidationException("Army Forge request timed out. Please try again.")
            except httpx.RequestError as e:
                logger.error(f"Request error fetching Army Forge list {list_id}: {str(e)}")
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
        
        # Clear existing units for this player using a separate query
        existing_units_stmt = select(Unit).where(Unit.player_id == player.id)
        existing_units_result = await session.execute(existing_units_stmt)
        existing_units = existing_units_result.scalars().all()
        
        if existing_units:
            logger.info(f"Clearing {len(existing_units)} existing units for player {player.name}")
            for unit in existing_units:
                await session.delete(unit)
        
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
                    raise ValidationException(
                        f"Army Forge server error - the list may be invalid or expired. "
                        f"Try re-sharing your list from Army Forge."
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
                logger.error(f"Error creating unit '{unit_data.get('name', 'Unknown')}': {str(e)}")
                raise ValidationException(f"Failed to import unit '{unit_data.get('name', 'Unknown')}': {str(e)}")
            units_created += 1
        
        # Store values for response before any commits
        player_name = player.name
        player_id = player.id
        game_id = game.id
        game_code = game.code  # cache code to avoid lazy load after commit
        current_round = game.current_round
        
        # Update player
        player.army_forge_list_id = list_id
        army_name = f"Imported Army ({units_created} units)"
        player.army_name = army_name
        player.starting_unit_count = units_created
        player.starting_points = total_points
        
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
