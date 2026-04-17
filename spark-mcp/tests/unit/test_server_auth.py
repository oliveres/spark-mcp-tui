"""Tests for BearerAuthMiddleware + RateLimitMiddleware timing-safe behavior."""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.testclient import TestClient

from spark_mcp.server import BearerAuthMiddleware, RateLimitMiddleware


def _build_app(*, protect_metrics: bool, token: str = "sk-spark-test-" + "a" * 32) -> Starlette:
    async def ok(_: Request) -> Response:
        return JSONResponse({"ok": True})

    routes = [
        Route("/mcp/", ok, methods=["GET", "POST"]),
        Route("/metrics", ok),
        Route("/health", ok),
    ]
    middleware = [
        Middleware(RateLimitMiddleware, requests_per_minute=5),
        Middleware(
            BearerAuthMiddleware,
            token=token,
            protect_metrics=protect_metrics,
        ),
    ]
    return Starlette(routes=routes, middleware=middleware)


def test_mcp_requires_auth() -> None:
    app = _build_app(protect_metrics=True)
    client = TestClient(app)
    assert client.get("/mcp/").status_code == 401


def test_mcp_accepts_valid_token() -> None:
    token = "sk-spark-valid-" + "b" * 32
    app = _build_app(protect_metrics=True, token=token)
    client = TestClient(app)
    resp = client.get("/mcp/", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_health_public() -> None:
    app = _build_app(protect_metrics=True)
    client = TestClient(app)
    assert client.get("/health").status_code == 200


def test_metrics_protected_by_default() -> None:
    app = _build_app(protect_metrics=True)
    client = TestClient(app)
    assert client.get("/metrics").status_code == 401


def test_metrics_public_when_auth_none() -> None:
    app = _build_app(protect_metrics=False)
    client = TestClient(app)
    assert client.get("/metrics").status_code == 200


def test_rate_limit_returns_429() -> None:
    app = _build_app(protect_metrics=True)
    client = TestClient(app)
    for _ in range(5):
        client.get("/health")
    assert client.get("/health").status_code == 429


def test_wrong_token_rejected() -> None:
    app = _build_app(protect_metrics=True, token="sk-spark-correct-" + "c" * 32)
    client = TestClient(app)
    resp = client.get("/mcp/", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


@pytest.mark.parametrize(
    "bad_header",
    ["", "bearer sk-spark-valid-" + "b" * 32, "Basic dXNlcjpwYXNz", "Bearer "],
)
def test_invalid_headers_rejected(bad_header: str) -> None:
    app = _build_app(protect_metrics=True, token="sk-spark-valid-" + "b" * 32)
    client = TestClient(app)
    resp = client.get("/mcp/", headers={"Authorization": bad_header})
    assert resp.status_code == 401
