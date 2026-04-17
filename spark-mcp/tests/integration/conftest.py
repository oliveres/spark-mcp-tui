"""Integration fixtures. Skipped unless env vars are configured."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session")
def integration_host() -> str:
    host = os.environ.get("SPARK_INTEGRATION_HOST")
    if not host:
        pytest.skip("SPARK_INTEGRATION_HOST not set")
    return host


@pytest.fixture(scope="session")
def integration_token() -> str:
    token = os.environ.get("SPARK_INTEGRATION_TOKEN")
    if not token:
        pytest.skip("SPARK_INTEGRATION_TOKEN not set")
    return token


@pytest.fixture(scope="session")
def integration_recipe() -> str:
    return os.environ.get("SPARK_INTEGRATION_RECIPE", "gemma4-26b-a4b")
