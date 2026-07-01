"""Content-free operator metrics: aggregate counts + the admin-token gate."""
from starlette.testclient import TestClient

from wingman.cloud import server_http, store_pg
from wingman.cloud.config_cloud import CloudConfig

A = "user_A"


def _cfg():
    return CloudConfig(
        database_url="x", workos_api_key="x", workos_client_id="x",
        base_url="https://smoke.test", allowed_origins=[], sentry_dsn=None,
        posthog_key=None, max_plans_per_user=100, max_tasks_per_plan=500,
        max_batch_size=50, max_body_bytes=262144,
    )


class _V:
    def verify(self, token):
        return {"sub": "x"}


async def test_global_stats_is_content_free_counts(pg_pool):
    await store_pg.upsert_user(A, "a@x.com", "Alice")
    await store_pg.create_plan(A, "P", ["t1", "t2", "t3"], max_plans=100, max_tasks=500)
    ids = [t["id"] for t in (await store_pg.get_plan(A, "P"))["tasks"]]
    await store_pg.tick_task(A, "P", ids[0])

    s = await store_pg.global_stats()
    assert s["total_users"] == 1
    assert s["total_plans"] == 1
    assert s["total_tasks"] == 3
    assert s["completed_tasks"] == 1
    assert s["pending_tasks"] == 2
    assert s["avg_hours_to_complete"] is None or s["avg_hours_to_complete"] >= 0
    # No content/name/email keys are present in the metrics payload.
    assert set(s) == {
        "total_users", "total_plans", "total_tasks",
        "completed_tasks", "pending_tasks", "avg_hours_to_complete",
    }


def test_admin_stats_404_when_token_unset(monkeypatch):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    client = TestClient(server_http.build_app(_cfg(), _V()))
    assert client.get("/admin/stats").status_code == 404


def test_admin_stats_requires_correct_token(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "s3cret")
    client = TestClient(server_http.build_app(_cfg(), _V()))
    assert client.get("/admin/stats").status_code == 401
    assert client.get("/admin/stats", headers={"x-admin-token": "wrong"}).status_code == 401
