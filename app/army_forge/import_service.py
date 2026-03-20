"""Import Army Forge lists into a game (DB + events)."""

import logging
import uuid
from datetime import datetime, timezone

import httpx
from litestar.exceptions import NotFoundException, ValidationException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.websocket import broadcast_to_game
from app.army_forge.import_fetch import download_army_forge_list, fetch_first_army_book_json
from app.army_forge.parse import (
    extract_list_id,
    parse_loadout_for_caster,
    parse_special_rules,
    parse_upgrades_for_caster,
)
from app.army_forge.schemas import ImportArmyResponse
from app.models import (
    DeploymentStatus,
    EventType,
    Game,
    GameEvent,
    Player,
    Unit,
    UnitState,
)
from app.utils.logging import error_log

logger = logging.getLogger("Herald.army_import")


async def import_army_into_game(
    session: AsyncSession,
    game_code: str,
    data_player_id: uuid.UUID,
    army_forge_url: str,
) -> ImportArmyResponse:
    """Fetch list from Army Forge and create units for the player."""
    stmt = (
        select(Game)
        .where(Game.code == game_code.upper())
        .options(selectinload(Game.players))
    )
    result = await session.execute(stmt)
    game = result.scalar_one_or_none()

    if not game:
        logger.warning("Game not found: %s", game_code)
        raise NotFoundException(f"Game with code '{game_code}' not found")

    player = None
    for p in game.players:
        if p.id == data_player_id:
            player = p
            break

    if not player:
        logger.warning("Player %s not found in game %s", data_player_id, game_code)
        raise NotFoundException(f"Player {data_player_id} not found in game")

    game.last_activity_at = datetime.now(timezone.utc)

    try:
        list_id = extract_list_id(army_forge_url)
        logger.debug("Extracted list ID: %s", list_id)
    except ValidationException:
        logger.error("Failed to extract list ID from: %s", army_forge_url)
        raise

    # One AsyncClient for TTS/share/book chain and the post-import army-book probe.
    async with httpx.AsyncClient() as client:
        army_data = await download_army_forge_list(client, list_id, logger)
        units_data = army_data.get("units", [])
        total_points = 0
        units_created = 0
    
        logger.info("Processing %s units from Army Forge", len(units_data))
    
        selection_id_to_unit: dict[str, Unit] = {}
        unit_id_to_state: dict[uuid.UUID, UnitState] = {}
        unit_data_with_attachments: list[tuple[dict, Unit]] = []
        unit_data_combined: list[tuple[dict, Unit]] = []
    
        for unit_data in units_data:
            try:
                rules = unit_data.get("rules", [])
                props = parse_special_rules(rules)
                loadout_raw = unit_data.get("loadout", [])
                loadout_is_caster, loadout_caster_level, loadout_filtered, rules_from_loadout = parse_loadout_for_caster(
                    loadout_raw
                )
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
                logger.debug(
                    "Creating unit: %s (Q%s+ D%s+)",
                    unit_name,
                    unit_data.get("quality", 4),
                    unit_data.get("defense", 4),
                )
    
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
                await session.flush()
    
                selection_id = unit_data.get("selectionId")
                if selection_id:
                    selection_id_to_unit[selection_id] = unit
    
                if unit_data.get("joinToUnit"):
                    if unit_data.get("combined") and not props["is_hero"]:
                        unit_data_combined.append((unit_data, unit))
                    else:
                        unit_data_with_attachments.append((unit_data, unit))
    
                initial_deployment = DeploymentStatus.IN_AMBUSH if props["has_ambush"] else DeploymentStatus.DEPLOYED
    
                unit_notes = unit_data.get("notes")
                if isinstance(unit_notes, str):
                    unit_notes = unit_notes.strip() or None
                    if unit_notes and len(unit_notes) > 500:
                        unit_notes = unit_notes[:497] + "..."
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
                        "unit_name": unit_data.get("name", "Unknown"),
                        "game_code": game_code,
                        "player_id": str(data_player_id),
                    },
                )
                raise ValidationException(
                    f"Failed to import unit '{unit_data.get('name', 'Unknown')}': {str(e)}"
                ) from e
            units_created += 1
    
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
                    "Merged combined unit %s into %s (new size: %s)",
                    combined_unit.name,
                    parent_unit.name,
                    parent_unit.size,
                )
            else:
                logger.warning(
                    "Could not find parent unit with selectionId '%s' for combined unit '%s' — keeping as separate unit",
                    join_to_selection_id,
                    combined_unit.name,
                )
    
        for unit_data, attached_unit in unit_data_with_attachments:
            join_to_selection_id = unit_data.get("joinToUnit")
            if join_to_selection_id and join_to_selection_id in selection_id_to_unit:
                parent_unit = selection_id_to_unit[join_to_selection_id]
                attached_unit.attached_to_unit_id = parent_unit.id
                logger.debug("Linked hero %s to %s", attached_unit.name, parent_unit.name)
            else:
                logger.warning(
                    "Could not find parent unit with selectionId '%s' for attached unit '%s'",
                    join_to_selection_id,
                    attached_unit.name,
                )
    
        list_points = army_data.get("listPoints")
        if list_points is not None and isinstance(list_points, (int, float)) and list_points >= 0:
            total_points = int(list_points)
            logger.debug("Using listPoints from API: %s", total_points)
    
        player_name = player.name
        player_id = player.id
        game_id = game.id
        game_code_cached = game.code
        current_round = game.current_round

        player.army_forge_list_id = list_id

        army_book = await fetch_first_army_book_json(
            client, units_data, army_data.get("gameSystem"), logger
        )

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
            parsed_spells.append(
                {
                    "name": spell_name,
                    "cost": token_cost,
                    "casting_roll": casting_roll,
                    "description": s.get("effect", s.get("description", s.get("text", ""))),
                }
            )
        player.spells = parsed_spells or None

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
            parsed_rules.append(
                {
                    "name": r["name"],
                    "description": r.get("description", ""),
                    "hasRating": r.get("hasRating", False),
                }
            )
        player.special_rules = parsed_rules or None

    player.starting_unit_count = (player.starting_unit_count or 0) + units_created
    player.starting_points = (player.starting_points or 0) + total_points

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

    logger.info("Import complete: %s units, %spts for player %s", units_created, total_points, player_name)

    await session.commit()

    await broadcast_to_game(
        game_code_cached,
        {
            "type": "state_update",
            "data": {"reason": "army_imported", "player_id": str(player_id)},
        },
    )

    return ImportArmyResponse(
        units_imported=units_created,
        army_name=army_name,
        total_points=total_points,
    )
