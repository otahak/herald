"""Unit tests for ``import_fetch`` with mocked httpx (no real network)."""

import logging
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.army_forge.import_fetch import download_army_forge_list, fetch_first_army_book_json
from litestar.exceptions import NotFoundException, ValidationException

LOG = logging.getLogger("test")


def _resp(status: int, json_data=None, text: str = ""):
    r = MagicMock()
    r.status_code = status
    r.text = text or ""
    if json_data is not None:
        r.json = MagicMock(return_value=json_data)

    def rf():
        if status >= 400:
            raise httpx.HTTPStatusError("err", request=MagicMock(), response=r)

    r.raise_for_status = MagicMock() if status < 400 else rf
    return r


@pytest.mark.asyncio
async def test_download_tts_success():
    client = AsyncMock()
    client.get = AsyncMock(return_value=_resp(200, {"units": [{"id": "1"}], "gameSystem": "gf"}))
    data = await download_army_forge_list(client, "list1", LOG)
    assert len(data["units"]) == 1


@pytest.mark.asyncio
async def test_download_tts_404():
    client = AsyncMock()
    client.get = AsyncMock(return_value=_resp(404, text="nope"))
    with pytest.raises(NotFoundException):
        await download_army_forge_list(client, "list1", LOG)


@pytest.mark.asyncio
async def test_download_tts_500_share_success():
    client = AsyncMock()

    async def getter(url, **kwargs):
        if "tts" in url:
            return _resp(500, text="fail")
        if "/api/share/" in url:
            return _resp(
                200,
                {
                    "gameSystem": "gf",
                    "units": [
                        {
                            "id": "u1",
                            "armyId": "a1",
                            "selectionId": "s1",
                            "selectedUpgrades": [],
                        }
                    ],
                },
            )
        if "army-books" in url:
            return _resp(200, {"units": [{"id": "u1", "name": "Troop"}], "name": "Book"})

    client.get = getter
    data = await download_army_forge_list(client, "list1", LOG)
    assert data["gameSystem"] == "gf"
    assert data["units"]


@pytest.mark.asyncio
async def test_download_tts_500_share_fails():
    client = AsyncMock()

    async def getter(url, **kwargs):
        if "tts" in url:
            return _resp(500, text="fail")
        if "/api/share/" in url:
            return _resp(500, text="share bad")

    client.get = getter
    with pytest.raises(ValidationException, match="Army Forge returned an error"):
        await download_army_forge_list(client, "list1", LOG)


@pytest.mark.asyncio
async def test_download_tts_500_book_fetch_fails_uses_placeholder():
    client = AsyncMock()

    async def getter(url, **kwargs):
        if "tts" in url:
            return _resp(500, text="fail")
        if "/api/share/" in url:
            return _resp(
                200,
                {
                    "gameSystem": "gf",
                    "units": [
                        {
                            "id": "u1",
                            "armyId": "a1",
                            "selectionId": "s1",
                            "selectedUpgrades": [],
                        }
                    ],
                },
            )
        if "army-books" in url:
            return _resp(500, text="book bad")

    client.get = getter
    data = await download_army_forge_list(client, "list1", LOG)
    assert data["units"] and data["units"][0].get("name")


@pytest.mark.asyncio
async def test_download_other_http_error():
    client = AsyncMock()
    client.get = AsyncMock(return_value=_resp(418, text="tea"))
    with pytest.raises(ValidationException, match="418"):
        await download_army_forge_list(client, "list1", LOG)


@pytest.mark.asyncio
async def test_download_timeout():
    client = AsyncMock()
    client.get = AsyncMock(side_effect=httpx.TimeoutException("t"))
    with pytest.raises(ValidationException, match="timed out"):
        await download_army_forge_list(client, "list1", LOG)


@pytest.mark.asyncio
async def test_download_request_error():
    client = AsyncMock()
    client.get = AsyncMock(side_effect=httpx.RequestError("e", request=MagicMock()))
    with pytest.raises(ValidationException, match="Failed to connect"):
        await download_army_forge_list(client, "list1", LOG)


@pytest.mark.asyncio
async def test_fetch_first_army_book_empty_game_system():
    client = AsyncMock()
    assert await fetch_first_army_book_json(client, [{"armyId": "x"}], None, LOG) == {}


@pytest.mark.asyncio
async def test_fetch_first_army_book_skips_and_succeeds():
    client = AsyncMock()

    async def getter(url, **kwargs):
        return _resp(200, {"name": "B", "versionString": "1", "spells": [], "specialRules": []})

    client.get = getter
    units = [
        "skip",
        {"armyId": "a1"},
        {"armyId": "a1"},
    ]
    book = await fetch_first_army_book_json(client, units, "gf", LOG)
    assert book.get("name") == "B"


@pytest.mark.asyncio
async def test_fetch_first_army_book_all_fail():
    client = AsyncMock()
    client.get = AsyncMock(return_value=_resp(500, text="x"))
    book = await fetch_first_army_book_json(client, [{"armyId": "a1"}], "gf", LOG)
    assert book == {}


@pytest.mark.asyncio
async def test_fetch_first_army_book_duplicate_army_id_skipped():
    client = AsyncMock()
    client.get = AsyncMock(return_value=_resp(500, text="x"))
    book = await fetch_first_army_book_json(
        client,
        [{"armyId": "a1"}, {"armyId": "a1"}],
        "gf",
        LOG,
    )
    assert book == {}


@pytest.mark.asyncio
async def test_fetch_first_army_book_skips_non_dict():
    client = AsyncMock()
    client.get = AsyncMock(return_value=_resp(200, {"name": "X"}))
    book = await fetch_first_army_book_json(client, ["x", {"armyId": "z1"}], "gf", LOG)
    assert book.get("name") == "X"
