"""Tests for ``app.army_forge.client.fetch_json_get`` (mocked httpx)."""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.army_forge.client import army_book_url, fetch_json_get, share_url, tts_url


def test_url_helpers():
    assert "tts?id=" in tts_url("abc")
    assert "/api/share/" in share_url("abc")
    assert "army-books" in army_book_url("aid", "gf")


@pytest.mark.asyncio
async def test_fetch_json_get_success():
    client = AsyncMock()
    resp = MagicMock()
    resp.json.return_value = {"units": []}
    resp.raise_for_status = MagicMock()
    client.get = AsyncMock(return_value=resp)
    data = await fetch_json_get(client, "http://example.com/x", timeout=5.0)
    assert data == {"units": []}


@pytest.mark.asyncio
async def test_fetch_json_get_404():
    from litestar.exceptions import NotFoundException

    client = AsyncMock()
    req = MagicMock()
    resp = MagicMock()
    resp.status_code = 404
    resp.text = "missing"
    err = httpx.HTTPStatusError("404", request=req, response=resp)
    client.get = AsyncMock(side_effect=err)
    with pytest.raises(NotFoundException, match="Custom missing"):
        await fetch_json_get(client, "http://x", not_found_detail="Custom missing")


@pytest.mark.asyncio
async def test_fetch_json_get_500():
    from litestar.exceptions import ValidationException

    client = AsyncMock()
    req = MagicMock()
    resp = MagicMock()
    resp.status_code = 500
    resp.text = "err"
    err = httpx.HTTPStatusError("500", request=req, response=resp)
    client.get = AsyncMock(side_effect=err)
    with pytest.raises(ValidationException, match="Army Forge returned an error"):
        await fetch_json_get(client, "http://x")


@pytest.mark.asyncio
async def test_fetch_json_get_other_status():
    from litestar.exceptions import ValidationException

    client = AsyncMock()
    req = MagicMock()
    resp = MagicMock()
    resp.status_code = 418
    resp.text = "tea"
    err = httpx.HTTPStatusError("418", request=req, response=resp)
    client.get = AsyncMock(side_effect=err)
    with pytest.raises(ValidationException, match="418"):
        await fetch_json_get(client, "http://x")


@pytest.mark.asyncio
async def test_fetch_json_get_timeout():
    from litestar.exceptions import ValidationException

    client = AsyncMock()
    client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
    with pytest.raises(ValidationException, match="timed out"):
        await fetch_json_get(client, "http://x")


@pytest.mark.asyncio
async def test_fetch_json_get_request_error():
    from litestar.exceptions import ValidationException

    client = AsyncMock()
    client.get = AsyncMock(side_effect=httpx.RequestError("boom", request=MagicMock()))
    with pytest.raises(ValidationException, match="Failed to connect"):
        await fetch_json_get(client, "http://x")
