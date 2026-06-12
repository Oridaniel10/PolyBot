"""pytest configuration: connect Redis before the test session."""

import pytest
from strategy import redis_store


@pytest.fixture(autouse=True, scope="session")
def redis_session_connect():
    """Connect Redis once per test session so all tests can use it."""
    redis_store.connect()
    yield
