"""HTTP-only steps for Army Forge import (used with a shared ``httpx.AsyncClient``)."""

import logging
from typing import Any

import httpx
from litestar.exceptions import NotFoundException, ValidationException

from app.army_forge.client import army_book_url, share_url, tts_url
from app.army_forge.parse import share_unit_to_tts


async def download_army_forge_list(
    client: httpx.AsyncClient,
    list_id: str,
    log: logging.Logger,
) -> dict[str, Any]:
    """
    Fetch TTS JSON for ``list_id``. On TTS HTTP 500, fall back to share API + army books
    (same client used for all follow-up GETs).
    """
    try:
        log.info("Fetching Army Forge list: %s", list_id)
        response = await client.get(tts_url(list_id), timeout=15.0)
        response.raise_for_status()
        data = response.json()
        log.debug("Army data received: %s units", len(data.get("units", [])))
        return data
    except httpx.HTTPStatusError as e:
        log.error("Army Forge HTTP error: %s", e.response.status_code)
        if e.response.status_code == 404:
            raise NotFoundException(f"Army list '{list_id}' not found on Army Forge") from e
        if e.response.status_code == 500:
            log.info("TTS API returned 500, trying share API fallback for %s", list_id)
            try:
                share_resp = await client.get(share_url(list_id), timeout=15.0)
                share_resp.raise_for_status()
                share_data = share_resp.json()
            except Exception as share_err:
                log.warning("Share API fallback failed: %s", share_err)
                raise ValidationException(
                    "Army Forge returned an error for this list. Check that the list and any custom or "
                    "Studio army books are set to public in Army Forge Studio. Otherwise add units manually."
                ) from share_err
            game_system = share_data.get("gameSystem", "gf")
            share_units = share_data.get("units", [])
            army_books: dict[str, dict] = {}
            for su in share_units:
                aid = su.get("armyId")
                if aid and aid not in army_books:
                    try:
                        book_resp = await client.get(
                            army_book_url(aid, game_system),
                            timeout=10.0,
                        )
                        book_resp.raise_for_status()
                        army_books[aid] = book_resp.json()
                    except Exception as book_err:
                        log.warning("Could not fetch army book %s: %s", aid, book_err)
                        army_books[aid] = {}
            book_units_by_id: dict[str, dict] = {}
            for aid, book in army_books.items():
                for u in book.get("units", []):
                    book_units_by_id[(aid, u.get("id"))] = u
            tts_units = []
            for su in share_units:
                aid = su.get("armyId")
                uid = su.get("id")
                book_unit = book_units_by_id.get((aid, uid)) if aid and uid else None
                book_full = (army_books.get(aid) if aid else None) or {}
                if book_unit is None and (aid or uid):
                    log.info(
                        "Army book unavailable for unit %s (army %s), using placeholder (list/army may be private)",
                        uid,
                        aid,
                    )
                tts_units.append(share_unit_to_tts(su, book_unit, book_full))
            army_data = {
                "gameSystem": game_system,
                "units": tts_units,
            }
            log.info("Share API fallback: converted %s units for %s", len(tts_units), list_id)
            return army_data
        raise ValidationException(f"Army Forge API error: {e.response.status_code}") from e
    except httpx.TimeoutException as e:
        log.error("Timeout fetching Army Forge list %s", list_id)
        raise ValidationException("Army Forge request timed out. Please try again.") from e
    except httpx.RequestError as e:
        log.error("Request error: %s", str(e))
        raise ValidationException(f"Failed to connect to Army Forge: {str(e)}") from e


async def fetch_first_army_book_json(
    client: httpx.AsyncClient,
    units_data: list,
    game_system: str | None,
    log: logging.Logger,
) -> dict[str, Any]:
    """
    Try each distinct ``armyId`` in ``units_data`` until one army-book GET succeeds.
    Returns ``{}`` if none succeed or ``game_system`` is missing.
    """
    army_book: dict[str, Any] = {}
    if not game_system:
        return army_book
    seen_army_ids: set[str] = set()
    for u in units_data:
        if not isinstance(u, dict):
            continue
        army_id = u.get("armyId")
        if not army_id or army_id in seen_army_ids:
            continue
        seen_army_ids.add(army_id)
        try:
            book_resp = await client.get(
                army_book_url(army_id, game_system),
                timeout=10.0,
            )
            book_resp.raise_for_status()
            army_book = book_resp.json()
            log.info(
                "Fetched army book '%s' v%s: %s spells, %s rules",
                army_book.get("name", "?"),
                army_book.get("versionString", "?"),
                len(army_book.get("spells", [])),
                len(army_book.get("specialRules", [])),
            )
            break
        except Exception as e:
            log.warning("Could not fetch army book for %s: %s", army_id, e)
    return army_book
