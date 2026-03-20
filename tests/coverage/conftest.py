"""Fixtures for sync Litestar ``TestClient`` (WebSockets, some admin flows)."""

import pytest
from litestar.testing import TestClient


@pytest.fixture
def sync_client(app):
    with TestClient(app=app) as client:
        yield client
