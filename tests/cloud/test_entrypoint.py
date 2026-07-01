"""The cloud entry point builds a servable ASGI app from env config."""
from wingman.cloud import __main__ as entry

REQUIRED = {
    "DATABASE_URL": "postgresql://u:p@localhost/db",
    "WORKOS_API_KEY": "sk",
    "WORKOS_CLIENT_ID": "cid",
    "WINGMAN_BASE_URL": "https://w.example.com",
}


def test_build_from_env_returns_app(monkeypatch):
    for k, v in REQUIRED.items():
        monkeypatch.setenv(k, v)

    # Do not actually connect: stub pool creation + init so no DB is needed.
    async def _fake_pool(dsn):
        return object()

    async def _fake_init(pool):
        return None

    monkeypatch.setattr(entry.store_pg, "create_pool", _fake_pool)
    monkeypatch.setattr(entry.store_pg, "init_db", _fake_init)

    app = entry.build_from_env(connect=False)
    assert app is not None
