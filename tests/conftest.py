import asyncio
import os
import sys
from pathlib import Path
from typing import AsyncIterator

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from asgi_lifespan import LifespanManager


@pytest_asyncio.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
def test_db_url(tmp_path_factory: pytest.TempPathFactory) -> str:
    db_path = tmp_path_factory.mktemp("db") / "test.db"
    return f"sqlite+aiosqlite:///{db_path}"


@pytest_asyncio.fixture(scope="session")
def app(test_db_url: str):
    # Ensure env is set before importing the app
    os.environ["DATABASE_URL"] = test_db_url
    os.environ["APP_DEBUG"] = "true"
    sys.path.append(str(Path(__file__).resolve().parents[1]))
    from app.main import app as litestar_app
    return litestar_app


@pytest_asyncio.fixture()
async def client(app) -> AsyncIterator[AsyncClient]:
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            yield ac

