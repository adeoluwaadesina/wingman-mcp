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


def _app(monkeypatch, resource_metadata_url=None):
    async def _noop_upsert(*a, **k):
        return None
    monkeypatch.setattr(server_http.store_pg, "upsert_user", _noop_upsert)
    routes = [Route("/whoami", _whoami)]
    app = Starlette(routes=routes)
    app.add_middleware(server_http.AuthMiddleware, verifier=_AllowVerifier(),
                       public_paths={"/healthz", "/.well-known/oauth-protected-resource"},
                       resource_metadata_url=resource_metadata_url)
    return app


def test_userinfo_enriches_email_and_name(monkeypatch):
    captured = {}

    async def _capture_upsert(uid, email, name):
        captured.update(uid=uid, email=email, name=name)

    async def _fake_userinfo(url, token):
        return {"email": "real@example.com", "name": "Real Name"}

    monkeypatch.setattr(server_http.store_pg, "upsert_user", _capture_upsert)
    monkeypatch.setattr(server_http.auth_mod, "fetch_userinfo", _fake_userinfo)
    app = Starlette(routes=[Route("/whoami", _whoami)])
    app.add_middleware(server_http.AuthMiddleware, verifier=_AllowVerifier(),
                       public_paths={"/healthz"}, userinfo_url="https://idp.example.com/userinfo")
    r = TestClient(app).get("/whoami", headers={"Authorization": "Bearer good"})
    assert r.status_code == 200
    # userinfo email/name override the (absent-here) token claims
    assert captured["email"] == "real@example.com"
    assert captured["name"] == "Real Name"


def test_401_carries_www_authenticate_resource_metadata(monkeypatch):
    url = "https://w.example.com/.well-known/oauth-protected-resource"
    client = TestClient(_app(monkeypatch, resource_metadata_url=url))
    resp = client.get("/whoami")  # no token
    assert resp.status_code == 401
    challenge = resp.headers.get("www-authenticate", "")
    assert f'resource_metadata="{url}"' in challenge


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


def test_handler_exception_resets_identity(monkeypatch):
    async def _boom(request):
        raise RuntimeError("downstream explosion")
    async def _noop_upsert(*a, **k):
        return None
    monkeypatch.setattr(server_http.store_pg, "upsert_user", _noop_upsert)
    app = Starlette(routes=[Route("/boom", _boom)])
    app.add_middleware(server_http.AuthMiddleware, verifier=_AllowVerifier(),
                       public_paths={"/healthz"})
    client = TestClient(app, raise_server_exceptions=False)
    client.get("/boom", headers={"Authorization": "Bearer good"})
    # identity must not leak across requests even when the handler raised
    with pytest.raises(identity.Unauthenticated):
        identity.current_user_id()


def test_verifier_crash_returns_503(monkeypatch):
    class _CrashVerifier:
        def verify(self, token):
            raise RuntimeError("jwks unreachable")
    async def _noop_upsert(*a, **k):
        return None
    monkeypatch.setattr(server_http.store_pg, "upsert_user", _noop_upsert)
    async def _ok(request):
        from starlette.responses import PlainTextResponse
        return PlainTextResponse("ok")
    app = Starlette(routes=[Route("/x", _ok)])
    app.add_middleware(server_http.AuthMiddleware, verifier=_CrashVerifier(),
                       public_paths={"/healthz"})
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/x", headers={"Authorization": "Bearer whatever"})
    assert r.status_code == 503
