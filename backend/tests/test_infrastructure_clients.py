"""Live connection checks for the infrastructure client layer.

Run explicitly while backend/compose.yml is up:
    RUN_INFRASTRUCTURE_TESTS=1 pytest -v tests/test_infrastructure_clients.py
"""

import os

import pytest

from app.infrastructure import (
    close_infrastructure,
    connect_infrastructure,
    healthcheck_infrastructure,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INFRASTRUCTURE_TESTS") != "1",
    reason="set RUN_INFRASTRUCTURE_TESTS=1 to test the live Compose services",
)


async def test_every_business_datastore_client_connects() -> None:
    try:
        await connect_infrastructure()
        health = await healthcheck_infrastructure()
        assert health == {
            "postgres": {"status": "ok"},
            "redis": {"status": "ok"},
            "minio": {"status": "ok"},
            "milvus": {"status": "ok"},
            "neo4j": {"status": "ok"},
        }
    finally:
        await close_infrastructure()
