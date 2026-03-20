"""Apply PATCH /units/{id} state changes and related event logging."""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.game_helpers import log_event
from app.api.game_schemas import UpdateUnitStateRequest
from app.models import DeploymentStatus, EventType, Game, GameEvent, Unit
from app.services.games.errors import UnitStateValidationError


async def apply_update_unit_state(
    session: AsyncSession,
    game: Game,
    unit: Unit,
    unit_id: uuid.UUID,
    data: UpdateUnitStateRequest,
) -> None:
    """Mutate unit.state and create/delete GameEvent rows as needed."""
    if data.wounds_taken is not None and data.wounds_taken != unit.state.wounds_taken:
        previous_state = {"wounds_taken": unit.state.wounds_taken}
        wound_diff = data.wounds_taken - unit.state.wounds_taken
        unit.state.wounds_taken = data.wounds_taken

        if wound_diff > 0:
            for i in range(wound_diff):
                wounds_at_this_point = previous_state["wounds_taken"] + i
                await log_event(
                    session,
                    game,
                    EventType.UNIT_WOUNDED,
                    f"{unit.display_name} took 1 wound ({unit.max_wounds - wounds_at_this_point - 1}/{unit.max_wounds} remaining)",
                    player_id=unit.player_id,
                    target_unit_id=unit.id,
                    details={
                        "wounds": 1,
                        "wounds_before": wounds_at_this_point,
                        "wounds_after": wounds_at_this_point + 1,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                    previous_state={"wounds_taken": wounds_at_this_point},
                )
        else:
            wounds_to_remove = abs(wound_diff)

            stmt = (
                select(GameEvent)
                .where(GameEvent.game_id == game.id)
                .where(GameEvent.event_type == EventType.UNIT_WOUNDED)
                .where(GameEvent.target_unit_id == unit_id)
                .where(GameEvent.is_undone == False)
                .order_by(GameEvent.created_at.desc())
                .limit(wounds_to_remove)
            )
            result = await session.execute(stmt)
            recent_wound_events = result.scalars().all()

            current_time = datetime.now(timezone.utc)
            threshold_time = current_time - timedelta(seconds=30)

            for event in recent_wound_events:
                if event.created_at >= threshold_time:
                    await session.delete(event)
                else:
                    await log_event(
                        session,
                        game,
                        EventType.UNIT_HEALED,
                        f"{unit.display_name} healed 1 wound",
                        player_id=unit.player_id,
                        target_unit_id=unit.id,
                        details={"wounds_healed": 1},
                    )

    if data.models_remaining is not None:
        unit.state.models_remaining = data.models_remaining

    if data.activated_this_round is not None and data.activated_this_round != unit.state.activated_this_round:
        if data.activated_this_round and unit.attached_to_unit_id:
            raise UnitStateValidationError(
                f"{unit.display_name} is attached to another unit and cannot be activated separately. "
                f"Activate the parent unit instead."
            )

        unit.state.activated_this_round = data.activated_this_round
        if data.activated_this_round:
            await log_event(
                session,
                game,
                EventType.UNIT_ACTIVATED,
                f"{unit.display_name} activated",
                player_id=unit.player_id,
                target_unit_id=unit.id,
            )

            if unit.attached_heroes:
                for attached_hero in unit.attached_heroes:
                    if attached_hero.state and not attached_hero.state.activated_this_round:
                        attached_hero.state.activated_this_round = True
                        await log_event(
                            session,
                            game,
                            EventType.UNIT_ACTIVATED,
                            f"{attached_hero.display_name} activated (attached to {unit.display_name})",
                            player_id=attached_hero.player_id,
                            target_unit_id=attached_hero.id,
                        )

    if data.is_shaken is not None and data.is_shaken != unit.state.is_shaken:
        unit.state.is_shaken = data.is_shaken
        if data.is_shaken:
            await log_event(
                session,
                game,
                EventType.STATUS_SHAKEN,
                f"{unit.display_name} became Shaken",
                player_id=unit.player_id,
                target_unit_id=unit.id,
            )
        else:
            await log_event(
                session,
                game,
                EventType.STATUS_SHAKEN_CLEARED,
                f"{unit.display_name} is no longer Shaken",
                player_id=unit.player_id,
                target_unit_id=unit.id,
            )

        if unit.attached_heroes:
            for attached_hero in unit.attached_heroes:
                if attached_hero.state and attached_hero.state.is_shaken != data.is_shaken:
                    attached_hero.state.is_shaken = data.is_shaken
                    if data.is_shaken:
                        await log_event(
                            session,
                            game,
                            EventType.STATUS_SHAKEN,
                            f"{attached_hero.display_name} became Shaken (attached to {unit.display_name})",
                            player_id=attached_hero.player_id,
                            target_unit_id=attached_hero.id,
                        )
                    else:
                        await log_event(
                            session,
                            game,
                            EventType.STATUS_SHAKEN_CLEARED,
                            f"{attached_hero.display_name} is no longer Shaken (attached to {unit.display_name})",
                            player_id=attached_hero.player_id,
                            target_unit_id=attached_hero.id,
                        )

    if data.is_fatigued is not None:
        unit.state.is_fatigued = data.is_fatigued
        if data.is_fatigued:
            await log_event(
                session,
                game,
                EventType.STATUS_FATIGUED,
                f"{unit.display_name} became Fatigued",
                target_unit_id=unit.id,
            )

    if data.deployment_status is not None and data.deployment_status != unit.state.deployment_status:
        old_status = unit.state.deployment_status
        unit.state.deployment_status = data.deployment_status

        if data.deployment_status == DeploymentStatus.DEPLOYED and old_status == DeploymentStatus.IN_AMBUSH:
            await log_event(
                session,
                game,
                EventType.UNIT_DEPLOYED,
                f"{unit.display_name} deployed from Ambush",
                target_unit_id=unit.id,
            )
        elif data.deployment_status == DeploymentStatus.DESTROYED:
            await log_event(
                session,
                game,
                EventType.UNIT_DESTROYED,
                f"{unit.display_name} was destroyed",
                target_unit_id=unit.id,
            )

            parent_was_shaken = unit.state.is_shaken
            if unit.attached_heroes:
                for attached_hero in unit.attached_heroes:
                    if parent_was_shaken and attached_hero.state:
                        if not attached_hero.state.is_shaken:
                            attached_hero.state.is_shaken = True
                            await log_event(
                                session,
                                game,
                                EventType.STATUS_SHAKEN,
                                f"{attached_hero.display_name} remains Shaken after detachment (parent was Shaken)",
                                player_id=attached_hero.player_id,
                                target_unit_id=attached_hero.id,
                            )

                    attached_hero.attached_to_unit_id = None
                    await log_event(
                        session,
                        game,
                        EventType.UNIT_DETACHED,
                        f"{attached_hero.display_name} detached from {unit.display_name} (parent destroyed)",
                        player_id=attached_hero.player_id,
                        target_unit_id=attached_hero.id,
                    )

            if unit.is_transport:
                all_units = [u for p in game.players for u in p.units]
                for passenger in all_units:
                    if passenger.state and passenger.state.transport_id == unit.id:
                        passenger.state.transport_id = None
                        passenger.state.deployment_status = DeploymentStatus.DEPLOYED
                        passenger.state.is_shaken = True
                        await log_event(
                            session,
                            game,
                            EventType.UNIT_DISEMBARKED,
                            (
                                f"{passenger.display_name} emergency disembarked from "
                                f"{unit.display_name} (destroyed) — Shaken, "
                                f"dangerous terrain test required"
                            ),
                            player_id=passenger.player_id,
                            target_unit_id=passenger.id,
                            details={"reason": "transport_destroyed", "dangerous_terrain_test": True},
                        )

    if "transport_id" in data.model_fields_set:
        if data.transport_id is not None:
            unit.state.transport_id = data.transport_id
            unit.state.deployment_status = DeploymentStatus.EMBARKED
            await log_event(
                session,
                game,
                EventType.UNIT_EMBARKED,
                f"{unit.display_name} embarked on transport",
                target_unit_id=unit.id,
            )
        elif unit.state.transport_id is not None:
            unit.state.transport_id = None
            unit.state.deployment_status = DeploymentStatus.DEPLOYED
            await log_event(
                session,
                game,
                EventType.UNIT_DISEMBARKED,
                f"{unit.display_name} disembarked from transport",
                target_unit_id=unit.id,
            )

    if data.spell_tokens is not None and data.spell_tokens != unit.state.spell_tokens:
        old_tokens = unit.state.spell_tokens
        unit.state.spell_tokens = min(6, max(0, data.spell_tokens))

        diff = unit.state.spell_tokens - old_tokens
        if diff > 0:
            await log_event(
                session,
                game,
                EventType.SPELL_TOKENS_GAINED,
                f"{unit.display_name} gained {diff} spell token(s) ({unit.state.spell_tokens}/6)",
                target_unit_id=unit.id,
                details={"tokens_gained": diff, "tokens_total": unit.state.spell_tokens},
            )
        elif diff < 0:
            await log_event(
                session,
                game,
                EventType.SPELL_TOKENS_SPENT,
                f"{unit.display_name} spent {-diff} spell token(s) ({unit.state.spell_tokens}/6)",
                target_unit_id=unit.id,
                details={"tokens_spent": -diff, "tokens_total": unit.state.spell_tokens},
            )

    if data.limited_weapons_used is not None:
        old_weapons = unit.state.limited_weapons_used or []
        unit.state.limited_weapons_used = data.limited_weapons_used

        new_weapons = set(data.limited_weapons_used) - set(old_weapons)
        for weapon in new_weapons:
            await log_event(
                session,
                game,
                EventType.LIMITED_WEAPON_USED,
                f"{unit.display_name} used {weapon} (Limited)",
                target_unit_id=unit.id,
                details={"weapon_name": weapon},
            )

    if data.custom_notes is not None:
        unit.state.custom_notes = data.custom_notes
