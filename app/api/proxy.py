"""Army Forge API proxy endpoints."""

import logging

import httpx
from litestar import Controller, get, post
from litestar.exceptions import HTTPException

from app.army_forge.client import fetch_json_get, tts_url
from app.army_forge.import_service import import_army_into_game
from app.army_forge.schemas import ArmyForgeListResponse, ImportArmyRequest, ImportArmyResponse
from app.utils.rate_limit import check_rate_limit
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("Herald.proxy")


class ProxyController(Controller):
    """Proxy endpoints for external API integration."""

    path = "/api/proxy"
    tags = ["proxy"]

    @get("/army-forge/{list_id:str}")
    async def get_army_forge_list(self, list_id: str) -> ArmyForgeListResponse:
        """Fetch an army list from Army Forge (avoids browser CORS)."""
        logger.info("Fetching Army Forge list: %s", list_id)
        logger.debug("Army Forge URL: %s", tts_url(list_id))

        async with httpx.AsyncClient() as client:
            data = await fetch_json_get(
                client,
                tts_url(list_id),
                timeout=15.0,
                not_found_detail=f"Army list '{list_id}' not found on Army Forge",
            )
            logger.info("Successfully fetched list %s: %s units", list_id, len(data.get("units", [])))
            return ArmyForgeListResponse(**data)

    @post("/import-army/{game_code:str}")
    async def import_army(
        self,
        game_code: str,
        data: ImportArmyRequest,
        session: AsyncSession,
    ) -> ImportArmyResponse:
        """Import an army from Army Forge into a game."""
        if not check_rate_limit(f"import_army:{game_code.upper()}", max_requests=10, window_sec=60):
            raise HTTPException(status_code=429, detail="Too many import requests. Please try again in a minute.")
        logger.info("Import army request for game %s, player %s", game_code, data.player_id)
        logger.debug("Army Forge URL/ID: %s", data.army_forge_url)
        return await import_army_into_game(
            session,
            game_code,
            data.player_id,
            data.army_forge_url,
        )
