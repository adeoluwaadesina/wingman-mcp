# tests/cloud/test_auth_middleware.py
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient
from wingman.cloud import server_http, auth, identity


class _AllowVerifier:
    def verify(self, token):
        if token == "good":
            return {"sub": "user_M", "email": "m@x.com", "name": "Em"}
        raise auth.InvalidToken("nope")


async def _whoami(request):
    return JSONResponse({"uid": identity.current_user_id()})


def _app(monkeypatch):
    async def _noop_upsert(*a, **k):
        return None
    monkeypatch.setattr(server_http.store_pg, "upsert_user", _noop_upsert)
    routes = [Route("/whoami", _whoami)]
    app = Starlette(routes=routes)
    app.add_middleware(server_http.AuthMiddleware, verifier=_AllowVerifier(),
                       public_paths={"/healthz", "/.well-known/oauth-protected-resource"})
    return app


def test_missing_token_401(monkeypatch):
    client = TestClient(_app(monkeypatch))
    assert client.get("/whoami").status_code == 401


def test_bad_token_401(monkeypatch):
    client = TestClient(_app(monkeypatch))
    assert client.get("/whoami", headers={"Authorization": "Bearer bad"}).status_code == 401


def test_good_token_sets_identity(monkeypatch):
    client = TestClient(_app(monkeypatch))
    r = client.get("/whoami", headers={"Authorization": "Bearer good"})
    assert r.status_code == 200
    assert r.json()["uid"] == "user_M"
