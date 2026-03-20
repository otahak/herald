"""HTTP client helpers for Army Forge public API."""

import httpx
from litestar.exceptions import NotFoundException, ValidationException

from app.utils.logging import error_log

BASE_URL = "https://army-forge.onepagerules.com"


def tts_url(list_id: str) -> str:
    return f"{BASE_URL}/api/tts?id={list_id}"


def share_url(list_id: str) -> str:
    return f"{BASE_URL}/api/share/{list_id}"


def army_book_url(army_id: str, game_system: str) -> str:
    return f"{BASE_URL}/api/army-books/{army_id}?gameSystem={game_system}"


async def fetch_json_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    timeout: float = 15.0,
    not_found_detail: str | None = None,
) -> dict:
    """GET JSON; map httpx errors to Litestar API exceptions."""
    try:
        response = await client.get(url, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        error_log(
            "Army Forge HTTP error",
            exc=e,
            context={
                "url": url,
                "status_code": e.response.status_code,
                "response_preview": e.response.text[:200] if e.response.text else "empty",
            },
        )
        if e.response.status_code == 404:
            raise NotFoundException(not_found_detail or "Army list not found on Army Forge") from e
        if e.response.status_code == 500:
            raise ValidationException(
                "Army Forge returned an error for this list. If it uses custom or Studio army books, "
                "check that the list and army are set to public in Army Forge Studio. Otherwise add units manually."
            ) from e
        raise ValidationException(f"Army Forge API error: {e.response.status_code}") from e
    except httpx.TimeoutException as e:
        error_log("Army Forge request timed out", exc=e, context={"url": url})
        raise ValidationException("Army Forge request timed out. Please try again.") from e
    except httpx.RequestError as e:
        error_log("Army Forge request failed", exc=e, context={"url": url})
        raise ValidationException(f"Failed to connect to Army Forge: {str(e)}") from e
