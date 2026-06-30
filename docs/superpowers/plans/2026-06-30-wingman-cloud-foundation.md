# Wingman Cloud Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn Wingman into a hosted, multi-tenant MCP server reachable over HTTPS so a user's plans/tasks sync across every device and LLM client, backed by one Postgres database, without touching the shipped local product.

**Architecture:** A new `src/wingman/cloud/` package sits beside the untouched local stdio code. It serves the same 13 LLM-visible tools over FastMCP streamable-HTTP, persists to Postgres (Neon) via async `asyncpg` with `user_id` scoping on every query, authenticates through WorkOS AuthKit (OAuth 2.1 resource server), and enforces the 2026-06-29 security threat model. Identity is derived server-side from the validated token and never travels as a tool argument.

**Tech Stack:** Python 3.10+, `mcp` (FastMCP), `asyncpg`, `PyJWT[crypto]`, `httpx`, Starlette/uvicorn (transitive via FastMCP streamable-http), optional `sentry-sdk` and `posthog`. Tests: `pytest`, `pytest-asyncio`.

## Global Constraints

- Local code is untouched: nothing under `cloud/` is imported by `src/wingman/server.py`, `src/wingman/storage/db.py`, or `src/wingman/__main__.py`. The existing local test suite must keep passing unchanged.
- Reuse, do not duplicate, pure logic: import `Plan`, `Task`, `TaskStatus`, `validate_plan_name` from `wingman.storage.models`; import `format_plan_text` and friends from `wingman.tools.plan_tools`; import prompt templates from `wingman.prompts`.
- No em dashes anywhere in code, comments, docs, or commit messages (use hyphens or commas).
- Every SQL statement parameterized with asyncpg `$1`,`$2` placeholders. Never f-string or `%` user data into SQL.
- `user_id` is ALWAYS sourced from the validated token via `identity.current_user_id()`, NEVER from a tool parameter or request body.
- Errors returned to clients are clean MCP `isError` results or HTTP error codes. No Python traceback ever reaches a client.
- Never log task/plan content, emails, or tokens. Do log auth failures, rate-limit hits, quota rejections with `user_id` + IP.
- Timestamps in cloud code use `datetime.now(timezone.utc)`, never `datetime.utcnow()`.
- Tool names and signatures exposed to the model are identical to local (same 13 LLM-visible names). `user_id` is not among any tool's parameters.
- All work on branch `feat/wingman-cloud` off `dev`. Nothing touches `main`.

---

## Pre-flight: create the working branch

Run once before Task 1:

```bash
cd "/c/Users/adeol/OneDrive/Documents/Adeolus Apps, websites and extensions/Wingman"
git checkout dev && git pull --ff-only
git checkout -b feat/wingman-cloud
```

A local Postgres is needed for the storage tests. Start one (Docker) and export its DSN. Any Postgres 14+ works; Neon test branch works too.

```bash
docker run -d --name wingman-pg -e POSTGRES_PASSWORD=pw -e POSTGRES_DB=wingman_test -p 5432:5432 postgres:16
export WINGMAN_TEST_DSN="postgresql://postgres:pw@localhost:5432/wingman_test"
```

---

## Task 1: Cloud package scaffold, dependencies, config

**Files:**
- Create: `src/wingman/cloud/__init__.py`
- Create: `src/wingman/cloud/config_cloud.py`
- Modify: `pyproject.toml` (add `[project.optional-dependencies] cloud`, `[project.scripts] wingman-cloud`, pytest-asyncio dev dep)
- Test: `tests/cloud/__init__.py`, `tests/cloud/test_config.py`

**Interfaces:**
- Produces: `config_cloud.CloudConfig` dataclass with fields `database_url: str`, `workos_api_key: str`, `workos_client_id: str`, `base_url: str`, `allowed_origins: list[str]`, `sentry_dsn: str | None`, `posthog_key: str | None`, `max_plans_per_user: int`, `max_tasks_per_plan: int`, `max_batch_size: int`, `max_body_bytes: int`. Classmethod `CloudConfig.from_env() -> CloudConfig` (raises `ConfigError` if a required var is missing). Module-level `ConfigError(Exception)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/cloud/test_config.py
import pytest
from wingman.cloud.config_cloud import CloudConfig, ConfigError

REQUIRED = {
    "DATABASE_URL": "postgresql://u:p@localhost/db",
    "WORKOS_API_KEY": "sk_test",
    "WORKOS_CLIENT_ID": "client_123",
    "WINGMAN_BASE_URL": "https://wingman.example.com",
}

def test_from_env_reads_required(monkeypatch):
    for k, v in REQUIRED.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    cfg = CloudConfig.from_env()
    assert cfg.database_url == REQUIRED["DATABASE_URL"]
    assert cfg.workos_client_id == "client_123"
    assert cfg.sentry_dsn is None

def test_defaults_applied(monkeypatch):
    for k, v in REQUIRED.items():
        monkeypatch.setenv(k, v)
    cfg = CloudConfig.from_env()
    assert cfg.max_plans_per_user == 100
    assert cfg.max_tasks_per_plan == 500
    assert cfg.max_batch_size == 50
    assert cfg.max_body_bytes == 256 * 1024

def test_quota_override(monkeypatch):
    for k, v in REQUIRED.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("MAX_PLANS_PER_USER", "7")
    assert CloudConfig.from_env().max_plans_per_user == 7

def test_allowed_origins_split(monkeypatch):
    for k, v in REQUIRED.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://a.com, https://b.com")
    assert CloudConfig.from_env().allowed_origins == ["https://a.com", "https://b.com"]

def test_missing_required_raises(monkeypatch):
    for k in REQUIRED:
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(ConfigError):
        CloudConfig.from_env()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/cloud/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: wingman.cloud.config_cloud`

- [ ] **Step 3: Create the package files**

```python
# src/wingman/cloud/__init__.py
"""Wingman Cloud: hosted, multi-tenant HTTP transport for Wingman.

Nothing in this package is imported by the local stdio product. See
docs/superpowers/specs/2026-06-30-wingman-cloud-foundation-design.md.
"""
```

```python
# src/wingman/cloud/config_cloud.py
"""Environment-driven configuration for Wingman Cloud."""
from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(Exception):
    """Raised when a required environment variable is missing."""


def _require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise ConfigError(f"missing required environment variable: {name}")
    return val


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


@dataclass(frozen=True)
class CloudConfig:
    database_url: str
    workos_api_key: str
    workos_client_id: str
    base_url: str
    allowed_origins: list[str]
    sentry_dsn: str | None
    posthog_key: str | None
    max_plans_per_user: int
    max_tasks_per_plan: int
    max_batch_size: int
    max_body_bytes: int

    @classmethod
    def from_env(cls) -> "CloudConfig":
        origins_raw = os.environ.get("ALLOWED_ORIGINS", "")
        origins = [o.strip() for o in origins_raw.split(",") if o.strip()]
        return cls(
            database_url=_require("DATABASE_URL"),
            workos_api_key=_require("WORKOS_API_KEY"),
            workos_client_id=_require("WORKOS_CLIENT_ID"),
            base_url=_require("WINGMAN_BASE_URL").rstrip("/"),
            allowed_origins=origins,
            sentry_dsn=os.environ.get("SENTRY_DSN") or None,
            posthog_key=os.environ.get("POSTHOG_KEY") or None,
            max_plans_per_user=_int_env("MAX_PLANS_PER_USER", 100),
            max_tasks_per_plan=_int_env("MAX_TASKS_PER_PLAN", 500),
            max_batch_size=_int_env("MAX_BATCH_SIZE", 50),
            max_body_bytes=_int_env("MAX_BODY_BYTES", 256 * 1024),
        )
```

Create empty `tests/cloud/__init__.py`.

Add to `pyproject.toml`:

```toml
[project.optional-dependencies]
cloud = [
    "asyncpg>=0.29",
    "PyJWT[crypto]>=2.8",
    "httpx>=0.27",
    "uvicorn>=0.30",
]
observability = [
    "sentry-sdk>=2.0",
    "posthog>=3.5",
]

[project.scripts]
wingman = "wingman.__main__:main"
wingman-cloud = "wingman.cloud.__main__:main"
```

Add `pytest-asyncio>=0.23` to the existing test/dev dependency group. In `pyproject.toml` under `[tool.pytest.ini_options]` add `asyncio_mode = "auto"`.

- [ ] **Step 4: Install extras and run test to verify it passes**

Run:
```bash
pip install -e ".[cloud,observability]" && pip install pytest-asyncio
pytest tests/cloud/test_config.py -v
```
Expected: PASS (5 passed)

- [ ] **Step 5: Confirm local suite still green**

Run: `pytest tests/ -q --ignore=tests/cloud`
Expected: PASS (existing 16 tests unchanged)

- [ ] **Step 6: Commit**

```bash
git add src/wingman/cloud/__init__.py src/wingman/cloud/config_cloud.py tests/cloud/ pyproject.toml
git commit -m "feat(cloud): package scaffold, cloud extras, env config"
```

---

## Task 2: Postgres schema, connection pool, init_db, test fixture

**Files:**
- Create: `migrations/001_init.sql`
- Create: `src/wingman/cloud/store_pg.py` (connection + schema portion)
- Create: `tests/cloud/conftest.py`
- Test: `tests/cloud/test_store_schema.py`

**Interfaces:**
- Produces:
  - `store_pg.SCHEMA_SQL: str` (the DDL text, loaded from `migrations/001_init.sql`).
  - `async store_pg.create_pool(dsn: str) -> asyncpg.Pool`
  - `async store_pg.init_db(pool) -> None` (idempotent, applies SCHEMA_SQL)
  - module exceptions: `PlanExists`, `PlanNotFound`, `TaskNotFound`, `QuotaExceeded(Exception)`
  - `store_pg.set_pool(pool)` / `store_pg.get_pool() -> asyncpg.Pool` (module-level current pool used by tool functions)

- [ ] **Step 1: Write the failing test**

```python
# tests/cloud/test_store_schema.py
import pytest
from wingman.cloud import store_pg

pytestmark = pytest.mark.asyncio

async def test_init_db_creates_tables(pg_pool):
    async with pg_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public'"
        )
    names = {r["table_name"] for r in rows}
    assert {"users", "plans", "tasks"} <= names

async def test_init_db_idempotent(pg_pool):
    # running init twice must not error
    await store_pg.init_db(pg_pool)
    await store_pg.init_db(pg_pool)
```

- [ ] **Step 2: Write the test fixture**

```python
# tests/cloud/conftest.py
import os
import pytest_asyncio
from wingman.cloud import store_pg

TEST_DSN = os.environ.get("WINGMAN_TEST_DSN")

@pytest_asyncio.fixture
async def pg_pool():
    if not TEST_DSN:
        import pytest
        pytest.skip("set WINGMAN_TEST_DSN to run cloud storage tests")
    pool = await store_pg.create_pool(TEST_DSN)
    await store_pg.init_db(pool)
    store_pg.set_pool(pool)
    # clean slate for each test
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE tasks, plans, users RESTART IDENTITY CASCADE")
    try:
        yield pool
    finally:
        await pool.close()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/cloud/test_store_schema.py -v`
Expected: FAIL with `AttributeError: module 'wingman.cloud.store_pg' has no attribute 'create_pool'`

- [ ] **Step 4: Write the DDL and connection layer**

```sql
-- migrations/001_init.sql
CREATE TABLE IF NOT EXISTS users (
    user_id      TEXT PRIMARY KEY,
    email        TEXT,
    display_name TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS plans (
    user_id     TEXT NOT NULL REFERENCES users(user_id),
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, name)
);

CREATE TABLE IF NOT EXISTS tasks (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id      TEXT NOT NULL,
    plan_name    TEXT NOT NULL,
    content      TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending','in_progress','done','blocked')),
    sort_order   INTEGER NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ NULL,
    FOREIGN KEY (user_id, plan_name) REFERENCES plans(user_id, name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tasks_plan ON tasks(user_id, plan_name, sort_order);
```

```python
# src/wingman/cloud/store_pg.py
"""Async Postgres persistence for Wingman Cloud.

Every function is scoped by user_id. user_id is supplied by the caller from
the validated session (see identity.py), never from a tool argument. All SQL
is parameterized. Mirrors the semantics of wingman.storage.db but multi-tenant.
"""
from __future__ import annotations

from pathlib import Path

import asyncpg

_MIGRATION = Path(__file__).resolve().parents[3] / "migrations" / "001_init.sql"
SCHEMA_SQL = _MIGRATION.read_text(encoding="utf-8")

_POOL: asyncpg.Pool | None = None


class PlanExists(Exception):
    pass


class PlanNotFound(Exception):
    pass


class TaskNotFound(Exception):
    pass


class QuotaExceeded(Exception):
    pass


async def create_pool(dsn: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(dsn, min_size=1, max_size=10)


async def init_db(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)


def set_pool(pool: asyncpg.Pool) -> None:
    global _POOL
    _POOL = pool


def get_pool() -> asyncpg.Pool:
    if _POOL is None:
        raise RuntimeError("store_pg pool not initialized")
    return _POOL
```

Note on `_MIGRATION` path: `parents[3]` resolves `src/wingman/cloud/store_pg.py` up to the repo root. Verify with the test; if the installed layout differs, package the SQL via `importlib.resources` instead (see Task 11 packaging note).

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/cloud/test_store_schema.py -v`
Expected: PASS (2 passed) when `WINGMAN_TEST_DSN` is set; SKIPPED otherwise.

- [ ] **Step 6: Commit**

```bash
git add migrations/001_init.sql src/wingman/cloud/store_pg.py tests/cloud/conftest.py tests/cloud/test_store_schema.py
git commit -m "feat(cloud): postgres schema, async pool, init_db, test fixture"
```

---

## Task 3: store_pg users upsert + plan CRUD with tenant isolation

**Files:**
- Modify: `src/wingman/cloud/store_pg.py`
- Test: `tests/cloud/test_store_plans.py`

**Interfaces:**
- Produces (all `async`, all take `user_id: str` first):
  - `upsert_user(user_id, email: str | None, display_name: str | None) -> None`
  - `count_users() -> int`
  - `create_plan(user_id, name: str, tasks: list[str] | None, *, max_plans: int, max_tasks: int) -> dict` returns the plan dict (same shape as `wingman.storage.db.get_plan` serialized: see `plan_to_dict` reuse below)
  - `get_plan(user_id, name) -> dict`
  - `list_plans(user_id) -> list[dict]` rows of `{"name","total","done"}`
  - `rename_plan(user_id, current, new) -> dict`
  - `delete_plan(user_id, name) -> None`
- Consumes: `wingman.storage.models.validate_plan_name`, and `wingman.tools.plan_tools` serialization helpers. The plan/task dict shapes must match local so the panel and text rendering are identical. Reuse `wingman.storage.models.Plan`/`Task` to build, then the existing `task_to_dict`/`plan_to_dict` in `wingman/tools/plan_tools.py`.

Before writing, open `src/wingman/tools/plan_tools.py` and confirm the exact serializer names and the `Plan`/`Task` constructor signatures in `src/wingman/storage/models.py`. Use those exact names. The block below assumes `plan_tools.plan_to_dict(plan: Plan) -> dict`; if the real name differs, substitute it consistently.

- [ ] **Step 1: Write the failing test**

```python
# tests/cloud/test_store_plans.py
import pytest
from wingman.cloud import store_pg

pytestmark = pytest.mark.asyncio
A, B = "user_A", "user_B"

async def _seed_users(pool):
    await store_pg.upsert_user(A, "a@x.com", "Alice")
    await store_pg.upsert_user(B, "b@x.com", "Bob")

async def test_upsert_user_idempotent_and_counts(pg_pool):
    await store_pg.upsert_user(A, "a@x.com", "Alice")
    await store_pg.upsert_user(A, "a2@x.com", "Alice2")  # update
    assert await store_pg.count_users() == 1

async def test_create_and_get_plan(pg_pool):
    await _seed_users(pg_pool)
    await store_pg.create_plan(A, "Roadmap", ["t1", "t2"], max_plans=100, max_tasks=500)
    plan = await store_pg.get_plan(A, "Roadmap")
    assert plan["name"] == "Roadmap"
    assert [t["content"] for t in plan["tasks"]] == ["t1", "t2"]
    assert plan["tasks"][0]["position"] == 1

async def test_two_users_share_a_plan_name(pg_pool):
    await _seed_users(pg_pool)
    await store_pg.create_plan(A, "Roadmap", ["a-task"], max_plans=100, max_tasks=500)
    await store_pg.create_plan(B, "Roadmap", ["b-task"], max_plans=100, max_tasks=500)
    assert (await store_pg.get_plan(A, "Roadmap"))["tasks"][0]["content"] == "a-task"
    assert (await store_pg.get_plan(B, "Roadmap"))["tasks"][0]["content"] == "b-task"

async def test_user_b_cannot_read_user_a_plan(pg_pool):
    await _seed_users(pg_pool)
    await store_pg.create_plan(A, "Secret", ["x"], max_plans=100, max_tasks=500)
    with pytest.raises(store_pg.PlanNotFound):
        await store_pg.get_plan(B, "Secret")

async def test_user_b_cannot_delete_user_a_plan(pg_pool):
    await _seed_users(pg_pool)
    await store_pg.create_plan(A, "Secret", ["x"], max_plans=100, max_tasks=500)
    with pytest.raises(store_pg.PlanNotFound):
        await store_pg.delete_plan(B, "Secret")
    # A's plan still intact
    assert (await store_pg.get_plan(A, "Secret"))["name"] == "Secret"

async def test_plan_quota_enforced(pg_pool):
    await _seed_users(pg_pool)
    await store_pg.create_plan(A, "P1", [], max_plans=1, max_tasks=500)
    with pytest.raises(store_pg.QuotaExceeded):
        await store_pg.create_plan(A, "P2", [], max_plans=1, max_tasks=500)

async def test_rename_is_scoped_and_atomic(pg_pool):
    await _seed_users(pg_pool)
    await store_pg.create_plan(A, "Old", ["keep"], max_plans=100, max_tasks=500)
    await store_pg.rename_plan(A, "Old", "New")
    assert (await store_pg.get_plan(A, "New"))["tasks"][0]["content"] == "keep"
    with pytest.raises(store_pg.PlanNotFound):
        await store_pg.get_plan(A, "Old")

async def test_list_plans_scoped(pg_pool):
    await _seed_users(pg_pool)
    await store_pg.create_plan(A, "A1", ["x"], max_plans=100, max_tasks=500)
    await store_pg.create_plan(B, "B1", [], max_plans=100, max_tasks=500)
    names = {p["name"] for p in await store_pg.list_plans(A)}
    assert names == {"A1"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/cloud/test_store_plans.py -v`
Expected: FAIL with `AttributeError: ... has no attribute 'upsert_user'`

- [ ] **Step 3: Implement users + plan functions**

Append to `src/wingman/cloud/store_pg.py`:

```python
from datetime import datetime, timezone

from ..storage.models import Plan, Task, validate_plan_name
from ..tools import plan_tools  # reuse plan_to_dict / list serialization


async def upsert_user(user_id: str, email: str | None, display_name: str | None) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (user_id, email, display_name, last_seen_at)
            VALUES ($1, $2, $3, now())
            ON CONFLICT (user_id) DO UPDATE
              SET email = COALESCE(EXCLUDED.email, users.email),
                  display_name = COALESCE(EXCLUDED.display_name, users.display_name),
                  last_seen_at = now()
            """,
            user_id, email, display_name,
        )


async def count_users() -> int:
    pool = get_pool()
    async with pool.acquire() as conn:
        return int(await conn.fetchval("SELECT count(*) FROM users"))


def _row_to_task(r, position: int) -> Task:
    return Task(
        id=r["id"],
        plan_name=r["plan_name"],
        content=r["content"],
        status=r["status"],
        sort_order=r["sort_order"],
        position=position,
        created_at=r["created_at"],
        updated_at=r["updated_at"],
        completed_at=r["completed_at"],
    )


async def _load_plan(conn, user_id: str, name: str) -> Plan:
    prow = await conn.fetchrow(
        "SELECT name, created_at, updated_at FROM plans WHERE user_id = $1 AND name = $2",
        user_id, name,
    )
    if prow is None:
        raise PlanNotFound(f"plan '{name}' not found")
    trows = await conn.fetch(
        "SELECT id, plan_name, content, status, sort_order, created_at, updated_at, completed_at "
        "FROM tasks WHERE user_id = $1 AND plan_name = $2 ORDER BY sort_order ASC, id ASC",
        user_id, name,
    )
    return Plan(
        name=prow["name"],
        created_at=prow["created_at"],
        updated_at=prow["updated_at"],
        tasks=[_row_to_task(r, i + 1) for i, r in enumerate(trows)],
    )


async def get_plan(user_id: str, name: str) -> dict:
    name = validate_plan_name(name)
    pool = get_pool()
    async with pool.acquire() as conn:
        plan = await _load_plan(conn, user_id, name)
    return plan_tools.plan_to_dict(plan)


async def create_plan(user_id: str, name: str, tasks, *, max_plans: int, max_tasks: int) -> dict:
    name = validate_plan_name(name)
    tasks = tasks or []
    cleaned = []
    for c in tasks:
        c = (c or "").strip()
        if not c:
            continue
        if len(c) > 2000:
            raise ValueError("task content must be 1-2000 chars")
        cleaned.append(c)
    if len(cleaned) > max_tasks:
        raise QuotaExceeded(f"a plan may have at most {max_tasks} tasks")
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            count = await conn.fetchval("SELECT count(*) FROM plans WHERE user_id = $1", user_id)
            if count >= max_plans:
                raise QuotaExceeded(f"you may have at most {max_plans} plans")
            exists = await conn.fetchval(
                "SELECT 1 FROM plans WHERE user_id = $1 AND name = $2", user_id, name
            )
            if exists:
                raise PlanExists(f"plan '{name}' already exists")
            await conn.execute(
                "INSERT INTO plans (user_id, name) VALUES ($1, $2)", user_id, name
            )
            for idx, content in enumerate(cleaned):
                await conn.execute(
                    "INSERT INTO tasks (user_id, plan_name, content, status, sort_order) "
                    "VALUES ($1, $2, $3, 'pending', $4)",
                    user_id, name, content, idx,
                )
            plan = await _load_plan(conn, user_id, name)
    return plan_tools.plan_to_dict(plan)


async def list_plans(user_id: str) -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT p.name AS name,
                   count(t.id) AS total,
                   coalesce(sum(CASE WHEN t.status = 'done' THEN 1 ELSE 0 END), 0) AS done
            FROM plans p
            LEFT JOIN tasks t ON t.user_id = p.user_id AND t.plan_name = p.name
            WHERE p.user_id = $1
            GROUP BY p.name, p.updated_at
            ORDER BY p.updated_at DESC, p.name ASC
            """,
            user_id,
        )
    return [{"name": r["name"], "total": int(r["total"]), "done": int(r["done"])} for r in rows]


async def rename_plan(user_id: str, current_name: str, new_name: str) -> dict:
    current_name = validate_plan_name(current_name)
    new_name = validate_plan_name(new_name)
    if current_name == new_name:
        return await get_plan(user_id, current_name)
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            if not await conn.fetchval(
                "SELECT 1 FROM plans WHERE user_id = $1 AND name = $2", user_id, current_name
            ):
                raise PlanNotFound(f"plan '{current_name}' not found")
            if await conn.fetchval(
                "SELECT 1 FROM plans WHERE user_id = $1 AND name = $2", user_id, new_name
            ):
                raise PlanExists(f"plan '{new_name}' already exists")
            await conn.execute(
                "INSERT INTO plans (user_id, name, created_at) "
                "SELECT user_id, $3, created_at FROM plans WHERE user_id = $1 AND name = $2",
                user_id, current_name, new_name,
            )
            await conn.execute(
                "UPDATE tasks SET plan_name = $3 WHERE user_id = $1 AND plan_name = $2",
                user_id, current_name, new_name,
            )
            await conn.execute(
                "DELETE FROM plans WHERE user_id = $1 AND name = $2", user_id, current_name
            )
            await conn.execute(
                "UPDATE plans SET updated_at = now() WHERE user_id = $1 AND name = $2",
                user_id, new_name,
            )
            plan = await _load_plan(conn, user_id, new_name)
    return plan_tools.plan_to_dict(plan)


async def delete_plan(user_id: str, name: str) -> None:
    name = validate_plan_name(name)
    pool = get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM plans WHERE user_id = $1 AND name = $2", user_id, name
        )
    # asyncpg returns e.g. "DELETE 1"
    if result.split()[-1] == "0":
        raise PlanNotFound(f"plan '{name}' not found")
```

If `plan_tools.plan_to_dict` does not exist under that name, use the actual serializer used by `plan_tools.get_plan` (inspect the file) and keep it consistent across all cloud tasks.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/cloud/test_store_plans.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add src/wingman/cloud/store_pg.py tests/cloud/test_store_plans.py
git commit -m "feat(cloud): user upsert + tenant-scoped plan CRUD with quotas"
```

---

## Task 4: store_pg task operations with ownership checks + quotas

**Files:**
- Modify: `src/wingman/cloud/store_pg.py`
- Test: `tests/cloud/test_store_tasks.py`

**Interfaces:**
- Produces (all `async`, `user_id` first):
  - `add_task(user_id, plan_name, content, *, max_tasks) -> dict` (task dict)
  - `add_tasks(user_id, plan_name, contents, *, max_tasks, max_batch) -> list[dict]`
  - `update_task_status(user_id, plan_name, task_id, status) -> dict`
  - `tick_task(user_id, plan_name, task_id) -> dict`
  - `delete_task(user_id, plan_name, task_id) -> None`
  - `reorder_tasks(user_id, plan_name, ordered_ids) -> dict` (plan dict)
  - `clear_completed(user_id, plan_name) -> int`
  - `clear_all(user_id, plan_name) -> int`
- Consumes: `wingman.tools.plan_tools` task serializer (e.g. `task_to_dict`); confirm exact name in the file.

- [ ] **Step 1: Write the failing test**

```python
# tests/cloud/test_store_tasks.py
import pytest
from wingman.cloud import store_pg

pytestmark = pytest.mark.asyncio
A, B = "user_A", "user_B"

async def _setup(pool):
    await store_pg.upsert_user(A, "a@x.com", "Alice")
    await store_pg.upsert_user(B, "b@x.com", "Bob")
    await store_pg.create_plan(A, "P", ["one", "two"], max_plans=100, max_tasks=500)

async def _ids(user, plan):
    return [t["id"] for t in (await store_pg.get_plan(user, plan))["tasks"]]

async def test_add_task(pg_pool):
    await _setup(pg_pool)
    await store_pg.add_task(A, "P", "three", max_tasks=500)
    assert [t["content"] for t in (await store_pg.get_plan(A, "P"))["tasks"]] == ["one", "two", "three"]

async def test_add_tasks_batch_cap(pg_pool):
    await _setup(pg_pool)
    with pytest.raises(store_pg.QuotaExceeded):
        await store_pg.add_tasks(A, "P", ["a", "b", "c"], max_tasks=500, max_batch=2)

async def test_add_task_plan_quota(pg_pool):
    await _setup(pg_pool)
    with pytest.raises(store_pg.QuotaExceeded):
        await store_pg.add_task(A, "P", "overflow", max_tasks=2)

async def test_tick_task(pg_pool):
    await _setup(pg_pool)
    tid = (await _ids(A, "P"))[0]
    res = await store_pg.tick_task(A, "P", tid)
    assert res["status"] == "done"
    assert res["completed_at"] is not None

async def test_user_b_cannot_tick_user_a_task(pg_pool):
    await _setup(pg_pool)
    tid = (await _ids(A, "P"))[0]
    # B owns no plan "P"; even with A's real task id, ownership fails
    await store_pg.upsert_user(B, None, None)
    with pytest.raises(store_pg.TaskNotFound):
        await store_pg.tick_task(B, "P", tid)
    # A's task still pending
    assert (await store_pg.get_plan(A, "P"))["tasks"][0]["status"] == "pending"

async def test_user_b_cannot_delete_user_a_task(pg_pool):
    await _setup(pg_pool)
    tid = (await _ids(A, "P"))[0]
    with pytest.raises(store_pg.TaskNotFound):
        await store_pg.delete_task(B, "P", tid)
    assert len((await store_pg.get_plan(A, "P"))["tasks"]) == 2

async def test_reorder(pg_pool):
    await _setup(pg_pool)
    ids = await _ids(A, "P")
    await store_pg.reorder_tasks(A, "P", list(reversed(ids)))
    assert [t["id"] for t in (await store_pg.get_plan(A, "P"))["tasks"]] == list(reversed(ids))

async def test_reorder_rejects_foreign_ids(pg_pool):
    await _setup(pg_pool)
    with pytest.raises(ValueError):
        await store_pg.reorder_tasks(A, "P", [999999])

async def test_clear_completed(pg_pool):
    await _setup(pg_pool)
    ids = await _ids(A, "P")
    await store_pg.tick_task(A, "P", ids[0])
    removed = await store_pg.clear_completed(A, "P")
    assert removed == 1
    assert len((await store_pg.get_plan(A, "P"))["tasks"]) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/cloud/test_store_tasks.py -v`
Expected: FAIL with `AttributeError: ... 'add_task'`

- [ ] **Step 3: Implement task functions**

Append to `src/wingman/cloud/store_pg.py`:

```python
VALID_STATUSES = {"pending", "in_progress", "done", "blocked"}


async def _get_task_dict(conn, user_id: str, task_id: int) -> dict:
    r = await conn.fetchrow(
        "SELECT id, plan_name, content, status, sort_order, created_at, updated_at, completed_at "
        "FROM tasks WHERE id = $1 AND user_id = $2",
        task_id, user_id,
    )
    if r is None:
        raise TaskNotFound(f"task {task_id} not found")
    return plan_tools.task_to_dict(_row_to_task(r, r["sort_order"] + 1))


async def _assert_plan(conn, user_id: str, plan_name: str) -> None:
    if not await conn.fetchval(
        "SELECT 1 FROM plans WHERE user_id = $1 AND name = $2", user_id, plan_name
    ):
        raise PlanNotFound(f"plan '{plan_name}' not found")


async def _count_tasks(conn, user_id: str, plan_name: str) -> int:
    return int(await conn.fetchval(
        "SELECT count(*) FROM tasks WHERE user_id = $1 AND plan_name = $2", user_id, plan_name
    ))


async def add_task(user_id: str, plan_name: str, content: str, *, max_tasks: int) -> dict:
    plan_name = validate_plan_name(plan_name)
    content = (content or "").strip()
    if not content or len(content) > 2000:
        raise ValueError("task content must be 1-2000 chars")
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _assert_plan(conn, user_id, plan_name)
            if await _count_tasks(conn, user_id, plan_name) >= max_tasks:
                raise QuotaExceeded(f"a plan may have at most {max_tasks} tasks")
            order = int(await conn.fetchval(
                "SELECT coalesce(max(sort_order), -1) + 1 FROM tasks WHERE user_id = $1 AND plan_name = $2",
                user_id, plan_name,
            ))
            tid = await conn.fetchval(
                "INSERT INTO tasks (user_id, plan_name, content, status, sort_order) "
                "VALUES ($1, $2, $3, 'pending', $4) RETURNING id",
                user_id, plan_name, content, order,
            )
            await conn.execute(
                "UPDATE plans SET updated_at = now() WHERE user_id = $1 AND name = $2",
                user_id, plan_name,
            )
            return await _get_task_dict(conn, user_id, tid)


async def add_tasks(user_id: str, plan_name: str, contents, *, max_tasks: int, max_batch: int) -> list[dict]:
    plan_name = validate_plan_name(plan_name)
    cleaned = []
    for c in contents:
        c = (c or "").strip()
        if not c:
            continue
        if len(c) > 2000:
            raise ValueError("task content must be 1-2000 chars")
        cleaned.append(c)
    if len(cleaned) > max_batch:
        raise QuotaExceeded(f"at most {max_batch} tasks per call")
    if not cleaned:
        return []
    pool = get_pool()
    out_ids = []
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _assert_plan(conn, user_id, plan_name)
            if await _count_tasks(conn, user_id, plan_name) + len(cleaned) > max_tasks:
                raise QuotaExceeded(f"a plan may have at most {max_tasks} tasks")
            order = int(await conn.fetchval(
                "SELECT coalesce(max(sort_order), -1) + 1 FROM tasks WHERE user_id = $1 AND plan_name = $2",
                user_id, plan_name,
            ))
            for content in cleaned:
                tid = await conn.fetchval(
                    "INSERT INTO tasks (user_id, plan_name, content, status, sort_order) "
                    "VALUES ($1, $2, $3, 'pending', $4) RETURNING id",
                    user_id, plan_name, content, order,
                )
                out_ids.append(tid)
                order += 1
            await conn.execute(
                "UPDATE plans SET updated_at = now() WHERE user_id = $1 AND name = $2",
                user_id, plan_name,
            )
            return [await _get_task_dict(conn, user_id, i) for i in out_ids]


async def update_task_status(user_id: str, plan_name: str, task_id: int, status: str) -> dict:
    plan_name = validate_plan_name(plan_name)
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {status}")
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            owned = await conn.fetchval(
                "SELECT 1 FROM tasks WHERE id = $1 AND user_id = $2 AND plan_name = $3",
                task_id, user_id, plan_name,
            )
            if not owned:
                raise TaskNotFound(f"task {task_id} not found in plan '{plan_name}'")
            if status == "done":
                await conn.execute(
                    "UPDATE tasks SET status = $1, completed_at = now(), updated_at = now() "
                    "WHERE id = $2 AND user_id = $3",
                    status, task_id, user_id,
                )
            else:
                await conn.execute(
                    "UPDATE tasks SET status = $1, completed_at = NULL, updated_at = now() "
                    "WHERE id = $2 AND user_id = $3",
                    status, task_id, user_id,
                )
            await conn.execute(
                "UPDATE plans SET updated_at = now() WHERE user_id = $1 AND name = $2",
                user_id, plan_name,
            )
            return await _get_task_dict(conn, user_id, task_id)


async def tick_task(user_id: str, plan_name: str, task_id: int) -> dict:
    return await update_task_status(user_id, plan_name, task_id, "done")


async def delete_task(user_id: str, plan_name: str, task_id: int) -> None:
    plan_name = validate_plan_name(plan_name)
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            result = await conn.execute(
                "DELETE FROM tasks WHERE id = $1 AND user_id = $2 AND plan_name = $3",
                task_id, user_id, plan_name,
            )
            if result.split()[-1] == "0":
                raise TaskNotFound(f"task {task_id} not found in plan '{plan_name}'")
            await conn.execute(
                "UPDATE plans SET updated_at = now() WHERE user_id = $1 AND name = $2",
                user_id, plan_name,
            )


async def reorder_tasks(user_id: str, plan_name: str, ordered_ids) -> dict:
    plan_name = validate_plan_name(plan_name)
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _assert_plan(conn, user_id, plan_name)
            rows = await conn.fetch(
                "SELECT id FROM tasks WHERE user_id = $1 AND plan_name = $2", user_id, plan_name
            )
            existing = {int(r["id"]) for r in rows}
            provided = [int(i) for i in ordered_ids]
            if set(provided) != existing:
                raise ValueError("reorder_tasks requires every task id of the plan, exactly once")
            for idx, tid in enumerate(provided):
                await conn.execute(
                    "UPDATE tasks SET sort_order = $1, updated_at = now() WHERE id = $2 AND user_id = $3",
                    idx, tid, user_id,
                )
            await conn.execute(
                "UPDATE plans SET updated_at = now() WHERE user_id = $1 AND name = $2",
                user_id, plan_name,
            )
            plan = await _load_plan(conn, user_id, plan_name)
    return plan_tools.plan_to_dict(plan)


async def clear_completed(user_id: str, plan_name: str) -> int:
    plan_name = validate_plan_name(plan_name)
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            result = await conn.execute(
                "DELETE FROM tasks WHERE user_id = $1 AND plan_name = $2 AND status = 'done'",
                user_id, plan_name,
            )
            await conn.execute(
                "UPDATE plans SET updated_at = now() WHERE user_id = $1 AND name = $2",
                user_id, plan_name,
            )
    return int(result.split()[-1])


async def clear_all(user_id: str, plan_name: str) -> int:
    plan_name = validate_plan_name(plan_name)
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            result = await conn.execute(
                "DELETE FROM tasks WHERE user_id = $1 AND plan_name = $2", user_id, plan_name
            )
            await conn.execute(
                "UPDATE plans SET updated_at = now() WHERE user_id = $1 AND name = $2",
                user_id, plan_name,
            )
    return int(result.split()[-1])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/cloud/test_store_tasks.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add src/wingman/cloud/store_pg.py tests/cloud/test_store_tasks.py
git commit -m "feat(cloud): tenant-scoped task ops with ownership checks and quotas"
```

---

## Task 5: Identity contextvar

**Files:**
- Create: `src/wingman/cloud/identity.py`
- Test: `tests/cloud/test_identity.py`

**Interfaces:**
- Produces:
  - `identity.set_current_user(user_id: str, email: str | None, display_name: str | None) -> contextvars.Token`
  - `identity.reset(token) -> None`
  - `identity.current_user_id() -> str` (raises `identity.Unauthenticated` if unset)
  - `identity.current_email() -> str | None`, `identity.current_display_name() -> str | None`
  - `identity.Unauthenticated(Exception)`

- [ ] **Step 1: Write the failing test**

```python
# tests/cloud/test_identity.py
import pytest
from wingman.cloud import identity

def test_unset_raises():
    with pytest.raises(identity.Unauthenticated):
        identity.current_user_id()

def test_set_and_read():
    tok = identity.set_current_user("u1", "e@x.com", "Eve")
    try:
        assert identity.current_user_id() == "u1"
        assert identity.current_email() == "e@x.com"
        assert identity.current_display_name() == "Eve"
    finally:
        identity.reset(tok)
    with pytest.raises(identity.Unauthenticated):
        identity.current_user_id()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/cloud/test_identity.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# src/wingman/cloud/identity.py
"""Request-scoped caller identity, derived from the validated token.

user_id NEVER comes from a tool argument. The auth middleware validates the
bearer token, then sets the identity here for the duration of the request.
Tool functions read current_user_id() to scope every storage call.
"""
from __future__ import annotations

import contextvars
from dataclasses import dataclass


class Unauthenticated(Exception):
    pass


@dataclass(frozen=True)
class _Identity:
    user_id: str
    email: str | None
    display_name: str | None


_current: contextvars.ContextVar[_Identity | None] = contextvars.ContextVar(
    "wingman_identity", default=None
)


def set_current_user(user_id: str, email: str | None, display_name: str | None) -> contextvars.Token:
    return _current.set(_Identity(user_id, email, display_name))


def reset(token: contextvars.Token) -> None:
    _current.reset(token)


def _get() -> _Identity:
    ident = _current.get()
    if ident is None:
        raise Unauthenticated("no authenticated user in context")
    return ident


def current_user_id() -> str:
    return _get().user_id


def current_email() -> str | None:
    return _get().email


def current_display_name() -> str | None:
    return _get().display_name
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/cloud/test_identity.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/wingman/cloud/identity.py tests/cloud/test_identity.py
git commit -m "feat(cloud): request-scoped identity contextvar"
```

---

## Task 6: Token verification (JWT/JWKS) and OAuth resource metadata

**Files:**
- Create: `src/wingman/cloud/auth.py`
- Test: `tests/cloud/test_auth.py`

**Interfaces:**
- Produces:
  - `auth.TokenVerifier(issuer: str, audience: str, jwks_uri: str)` with `verify(token: str) -> dict` returning claims; raises `auth.InvalidToken` on bad signature/expiry/audience/issuer. The JWKS lookup is via `jwt.PyJWKClient(jwks_uri)`; to keep it testable, the verifier resolves the signing key through `self._signing_key(token)`, which tests override.
  - `auth.InvalidToken(Exception)`
  - `auth.resource_metadata(base_url: str, authorization_servers: list[str]) -> dict` returning the `/.well-known/oauth-protected-resource` document.
- Consumes: `PyJWT[crypto]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/cloud/test_auth.py
import time
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from wingman.cloud import auth

ISSUER = "https://idp.example.com"
AUD = "https://wingman.example.com"

@pytest.fixture(scope="module")
def keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key

def _make_token(key, **over):
    claims = {"sub": "user_xyz", "iss": ISSUER, "aud": AUD,
              "exp": int(time.time()) + 300, "email": "z@x.com", "name": "Zed"}
    claims.update(over)
    return jwt.encode(claims, key, algorithm="RS256")

class _StubVerifier(auth.TokenVerifier):
    def __init__(self, pubkey):
        super().__init__(ISSUER, AUD, "https://idp.example.com/jwks")
        self._pub = pubkey
    def _signing_key(self, token):
        return self._pub

def test_valid_token_returns_sub(keypair):
    v = _StubVerifier(keypair.public_key())
    claims = v.verify(_make_token(keypair))
    assert claims["sub"] == "user_xyz"
    assert claims["email"] == "z@x.com"

def test_expired_token_rejected(keypair):
    v = _StubVerifier(keypair.public_key())
    with pytest.raises(auth.InvalidToken):
        v.verify(_make_token(keypair, exp=int(time.time()) - 10))

def test_wrong_audience_rejected(keypair):
    v = _StubVerifier(keypair.public_key())
    with pytest.raises(auth.InvalidToken):
        v.verify(_make_token(keypair, aud="https://evil.com"))

def test_bad_signature_rejected(keypair):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    v = _StubVerifier(keypair.public_key())
    with pytest.raises(auth.InvalidToken):
        v.verify(_make_token(other))

def test_resource_metadata_shape():
    doc = auth.resource_metadata(AUD, [ISSUER])
    assert doc["resource"] == AUD
    assert doc["authorization_servers"] == [ISSUER]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/cloud/test_auth.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# src/wingman/cloud/auth.py
"""OAuth 2.1 resource-server token verification for Wingman Cloud.

Wingman validates bearer tokens issued by the managed IdP (WorkOS AuthKit).
It does not issue tokens. The stable `sub` claim becomes the Wingman user_id.
"""
from __future__ import annotations

import jwt


class InvalidToken(Exception):
    pass


class TokenVerifier:
    def __init__(self, issuer: str, audience: str, jwks_uri: str):
        self._issuer = issuer
        self._audience = audience
        self._jwks_uri = jwks_uri
        self._jwk_client = jwt.PyJWKClient(jwks_uri) if jwks_uri else None

    def _signing_key(self, token: str):
        # Overridden in tests. In production, resolve the key from JWKS by the
        # token's `kid`. PyJWKClient caches keys internally.
        if self._jwk_client is None:
            raise InvalidToken("no JWKS client configured")
        return self._jwk_client.get_signing_key_from_jwt(token).key

    def verify(self, token: str) -> dict:
        try:
            key = self._signing_key(token)
            return jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "sub"]},
            )
        except InvalidToken:
            raise
        except Exception as exc:  # jwt.* errors, key errors, etc.
            raise InvalidToken(str(exc)) from exc


def resource_metadata(base_url: str, authorization_servers: list[str]) -> dict:
    """The /.well-known/oauth-protected-resource document (RFC 9728)."""
    return {
        "resource": base_url,
        "authorization_servers": authorization_servers,
        "bearer_methods_supported": ["header"],
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/cloud/test_auth.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/wingman/cloud/auth.py tests/cloud/test_auth.py
git commit -m "feat(cloud): JWT/JWKS token verifier + oauth resource metadata"
```

---

## Task 7: Cloud MCP server, tools wired to store_pg + identity

**Files:**
- Create: `src/wingman/cloud/server_http.py` (tool registration + app builder portion)
- Test: `tests/cloud/test_server_tools.py`

**Interfaces:**
- Produces:
  - `server_http.build_mcp(cfg: CloudConfig) -> FastMCP` registering the same 13 LLM-visible tools as local, each an `async def` that reads `identity.current_user_id()` and calls `store_pg`. Tool names: `create_plan, add_task, add_tasks, show_plan, get_plan, tick_task, update_task_status, rename_plan, reorder_tasks, list_plans, delete_plan, show_plans`. (Note: that is 12 names; the 13th LLM-visible surface is `show_plans` vs `show_plan` both panel-bound. Confirm the exact local set from `src/wingman/server.py` and match it one-for-one.)
  - `server_http.LLM_TOOL_NAMES: set[str]` for the parity test.
- Consumes: `store_pg`, `identity`, `config_cloud.CloudConfig`, rendering from `plan_tools`.

Open `src/wingman/server.py` and copy the exact list of `@mcp.tool(meta=MODEL_AND_APP...)` and `SHOW_PLAN_META` tool names. The cloud server must expose the identical set so clients see an unchanged Wingman. Do not register the `_ui_*` app-only tools in this task; they come in the panel follow-up and are not needed for Foundation parity with the model-visible surface. Record the exact local LLM-visible set in `LLM_TOOL_NAMES`.

- [ ] **Step 1: Write the failing test**

```python
# tests/cloud/test_server_tools.py
import pytest
from wingman.cloud import server_http, identity, store_pg
from wingman.cloud.config_cloud import CloudConfig

pytestmark = pytest.mark.asyncio

def _cfg():
    return CloudConfig(
        database_url="x", workos_api_key="x", workos_client_id="x",
        base_url="https://w.example.com", allowed_origins=[], sentry_dsn=None,
        posthog_key=None, max_plans_per_user=100, max_tasks_per_plan=500,
        max_batch_size=50, max_body_bytes=262144,
    )

async def test_tool_parity_with_local():
    # The cloud LLM-visible tool set equals the documented local set.
    from wingman import server as local_server
    mcp = local_server.build_server()
    local_tools = {t.name for t in (await mcp.list_tools()) if not t.name.startswith("_ui_")}
    assert server_http.LLM_TOOL_NAMES == local_tools

async def test_create_and_get_through_tools(pg_pool):
    tok = identity.set_current_user("user_T", "t@x.com", "Tee")
    try:
        await server_http.tool_create_plan(_cfg(), "Demo", ["a", "b"])
        plan = await server_http.tool_get_plan("Demo")
        assert [t["content"] for t in plan["tasks"]] == ["a", "b"]
    finally:
        identity.reset(tok)

async def test_tools_require_identity(pg_pool):
    with pytest.raises(identity.Unauthenticated):
        await server_http.tool_get_plan("Demo")
```

The test calls thin module-level helpers (`tool_create_plan`, `tool_get_plan`) that the registered tools delegate to, so the storage logic is testable without driving the full MCP transport. Register the FastMCP tools as one-line wrappers over these helpers.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/cloud/test_server_tools.py -v`
Expected: FAIL with `ModuleNotFoundError` / missing attributes

- [ ] **Step 3: Implement**

```python
# src/wingman/cloud/server_http.py
"""Cloud MCP server: same tools as local, served over streamable-HTTP,
scoped to the authenticated user and persisted to Postgres."""
from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from . import identity, store_pg
from .config_cloud import CloudConfig
from ..tools import plan_tools

LLM_TOOL_NAMES = {
    "create_plan", "add_task", "add_tasks", "show_plan", "get_plan",
    "tick_task", "update_task_status", "rename_plan", "reorder_tasks",
    "list_plans", "delete_plan", "show_plans",
}
# IMPORTANT: this set must equal the local model-visible tool names. Verify
# against src/wingman/server.py during implementation and adjust both sides
# together if they differ.


# --- thin, directly-testable helpers (identity read here) -------------------

async def tool_create_plan(cfg: CloudConfig, name: str, tasks: list[str] | None) -> dict[str, Any]:
    uid = identity.current_user_id()
    return await store_pg.create_plan(
        uid, name, tasks or [],
        max_plans=cfg.max_plans_per_user, max_tasks=cfg.max_tasks_per_plan,
    )

async def tool_get_plan(name: str) -> dict[str, Any]:
    return await store_pg.get_plan(identity.current_user_id(), name)

async def tool_list_plans() -> dict[str, Any]:
    plans = await store_pg.list_plans(identity.current_user_id())
    return plan_tools.list_plans_payload(plans)  # reuse local picker payload shape

async def tool_add_task(cfg: CloudConfig, plan_name: str, content: str) -> dict[str, Any]:
    return await store_pg.add_task(
        identity.current_user_id(), plan_name, content, max_tasks=cfg.max_tasks_per_plan
    )

async def tool_add_tasks(cfg: CloudConfig, plan_name: str, tasks: list[str]) -> dict[str, Any]:
    created = await store_pg.add_tasks(
        identity.current_user_id(), plan_name, tasks,
        max_tasks=cfg.max_tasks_per_plan, max_batch=cfg.max_batch_size,
    )
    return await store_pg.get_plan(identity.current_user_id(), plan_name)

async def tool_tick_task(plan_name: str, task_id: int) -> dict[str, Any]:
    return await store_pg.tick_task(identity.current_user_id(), plan_name, task_id)

async def tool_update_status(plan_name: str, task_id: int, status: str) -> dict[str, Any]:
    return await store_pg.update_task_status(identity.current_user_id(), plan_name, task_id, status)

async def tool_rename_plan(current_name: str, new_name: str) -> dict[str, Any]:
    return await store_pg.rename_plan(identity.current_user_id(), current_name, new_name)

async def tool_reorder(plan_name: str, ordered_ids: list[int]) -> dict[str, Any]:
    return await store_pg.reorder_tasks(identity.current_user_id(), plan_name, ordered_ids)

async def tool_delete_plan(name: str) -> dict[str, Any]:
    await store_pg.delete_plan(identity.current_user_id(), name)
    return {"deleted": name}


def build_mcp(cfg: CloudConfig) -> FastMCP:
    mcp = FastMCP(name="wingman", instructions=(
        "Wingman is an interactive plan/to-do panel for this conversation. "
        "Plans persist across messages and sync across your devices."
    ))

    @mcp.tool(description="Create a new named plan with optional initial tasks.")
    async def create_plan(name: str, tasks: list[str] | None = None) -> dict[str, Any]:
        return await tool_create_plan(cfg, name, tasks)

    @mcp.tool(description="Append a single task to a plan.")
    async def add_task(plan_name: str, content: str) -> dict[str, Any]:
        return await tool_add_task(cfg, plan_name, content)

    @mcp.tool(description="Append multiple tasks to a plan in one call.")
    async def add_tasks(plan_name: str, tasks: list[str]) -> dict[str, Any]:
        return await tool_add_tasks(cfg, plan_name, tasks)

    @mcp.tool(description="Return plan state as formatted text.")
    async def get_plan(plan_name: str) -> dict[str, Any]:
        return await tool_get_plan(plan_name)

    @mcp.tool(description="Mark a task as done.")
    async def tick_task(plan_name: str, task_id: int) -> dict[str, Any]:
        return await tool_tick_task(plan_name, task_id)

    @mcp.tool(description="Change a task's status.")
    async def update_task_status(plan_name: str, task_id: int, status: str) -> dict[str, Any]:
        return await tool_update_status(plan_name, task_id, status)

    @mcp.tool(description="Rename a plan.")
    async def rename_plan(current_name: str, new_name: str) -> dict[str, Any]:
        return await tool_rename_plan(current_name, new_name)

    @mcp.tool(description="Reorder tasks within a plan.")
    async def reorder_tasks(plan_name: str, ordered_ids: list[int]) -> dict[str, Any]:
        return await tool_reorder(plan_name, ordered_ids)

    @mcp.tool(description="List all plans with task counts.")
    async def list_plans() -> dict[str, Any]:
        return await tool_list_plans()

    @mcp.tool(description="Delete a plan and all its tasks.")
    async def delete_plan(plan_name: str) -> dict[str, Any]:
        return await tool_delete_plan(plan_name)

    # show_plan / show_plans: panel-bound tools. For Foundation they return the
    # same structuredContent + text the local versions do. Reuse local meta
    # shape from wingman.server (SHOW_PLAN_META) so the panel binds. Wire these
    # to the same payload builders used above. Implement to match local exactly.
    _register_panel_tools(mcp, cfg)
    return mcp
```

Add `_register_panel_tools` mirroring local `show_plan`/`show_plans` (return `CallToolResult` with the panel `_meta`, reusing `wingman.server._panel_result_meta` shape). If `plan_tools.list_plans_payload` does not exist, use whatever local `plan_tools.list_plans` returns and match it. Keep `LLM_TOOL_NAMES` equal to the local set.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/cloud/test_server_tools.py -v`
Expected: PASS (3 passed; parity test green confirms the cloud tool set equals local)

- [ ] **Step 5: Commit**

```bash
git add src/wingman/cloud/server_http.py tests/cloud/test_server_tools.py
git commit -m "feat(cloud): cloud MCP tools wired to store_pg + identity, parity with local"
```

---

## Task 8: Auth middleware (validate token, set identity, upsert user, 401)

**Files:**
- Modify: `src/wingman/cloud/server_http.py` (add ASGI app builder + auth middleware)
- Test: `tests/cloud/test_auth_middleware.py`

**Interfaces:**
- Produces:
  - `server_http.AuthMiddleware(app, verifier, public_paths: set[str])` Starlette `BaseHTTPMiddleware`: extracts `Authorization: Bearer`, validates via `verifier.verify`, on success sets `identity.set_current_user(...)` for the request and upserts the user, on failure returns `401` JSON. Paths in `public_paths` (the well-known metadata, health) skip auth.
  - `server_http.build_app(cfg, verifier) -> Starlette` mounting the FastMCP streamable-HTTP app plus the `/.well-known/oauth-protected-resource` and `/healthz` routes, wrapped in `AuthMiddleware`.

Note on contextvar propagation: `BaseHTTPMiddleware.dispatch` sets the identity before `call_next`, so it is visible to the downstream MCP handler within the same context. The Task 12 smoke test proves this end to end. If a future SDK version runs tools in a detached task group and loses the contextvar, fall back to reading identity from the MCP request context inside each tool; keep the helper layer so only the source of `current_user_id` changes.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/cloud/test_auth_middleware.py -v`
Expected: FAIL with `AttributeError: ... 'AuthMiddleware'`

- [ ] **Step 3: Implement**

Append to `src/wingman/cloud/server_http.py`:

```python
import logging

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from . import auth as auth_mod

log = logging.getLogger("wingman.cloud")


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, verifier, public_paths):
        super().__init__(app)
        self._verifier = verifier
        self._public = public_paths

    async def dispatch(self, request: Request, call_next):
        if request.url.path in self._public:
            return await call_next(request)
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        token = header.split(" ", 1)[1].strip()
        try:
            claims = self._verifier.verify(token)
        except auth_mod.InvalidToken:
            client = request.client.host if request.client else "?"
            log.warning("auth failure from ip=%s path=%s", client, request.url.path)
            return JSONResponse({"error": "invalid_token"}, status_code=401)
        uid = claims["sub"]
        tok = identity.set_current_user(uid, claims.get("email"), claims.get("name"))
        try:
            await store_pg.upsert_user(uid, claims.get("email"), claims.get("name"))
            return await call_next(request)
        finally:
            identity.reset(tok)


def build_app(cfg: CloudConfig, verifier) -> Starlette:
    mcp = build_mcp(cfg)
    mcp_app = mcp.streamable_http_app()  # ASGI sub-app

    async def well_known(request):
        return JSONResponse(auth_mod.resource_metadata(
            cfg.base_url, authorization_servers=[_idp_issuer(cfg)]
        ))

    async def healthz(request):
        return JSONResponse({"ok": True})

    routes = [
        Route("/.well-known/oauth-protected-resource", well_known),
        Route("/healthz", healthz),
    ]
    app = Starlette(routes=routes)
    app.mount("/", mcp_app)
    app.add_middleware(
        AuthMiddleware, verifier=verifier,
        public_paths={"/healthz", "/.well-known/oauth-protected-resource"},
    )
    return app


def _idp_issuer(cfg: CloudConfig) -> str:
    # WorkOS issuer for the configured client. Documented as an env-derived
    # value; for AuthKit this is the AuthKit domain. Wire from cfg/env.
    import os
    return os.environ.get("WORKOS_ISSUER", "https://api.workos.com")
```

Add `WORKOS_ISSUER` to the env list in `.env.example` (Task 11). If the streamable-HTTP app object exposes a different constructor name in the installed `mcp` version, use that (check `mcp.server.fastmcp.FastMCP`); the rest of the wiring is unchanged.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/cloud/test_auth_middleware.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/wingman/cloud/server_http.py tests/cloud/test_auth_middleware.py
git commit -m "feat(cloud): auth middleware, identity binding, user upsert, well-known metadata"
```

---

## Task 9: Hardening middleware (body cap, rate limit, CORS, security headers)

**Files:**
- Create: `src/wingman/cloud/hardening.py`
- Modify: `src/wingman/cloud/server_http.py` (apply hardening in `build_app`)
- Test: `tests/cloud/test_hardening.py`

**Interfaces:**
- Produces:
  - `hardening.RateLimiter(max_per_min: int)` with `allow(key: str) -> bool` (in-memory token bucket; key is `user_id` or IP).
  - `hardening.BodyLimitMiddleware(app, max_bytes)` -> 413 when `content-length` exceeds cap.
  - `hardening.SecurityHeadersMiddleware(app)` -> adds HSTS, `X-Content-Type-Options: nosniff`.
  - `hardening.RateLimitMiddleware(app, limiter)` -> 429 on breach, keyed by `identity.current_user_id()` if set else client IP; logs the hit.
  - `hardening.apply(app, cfg)` adds CORS (from `cfg.allowed_origins`, never `*`), body limit, rate limit, and security headers in the correct order.

- [ ] **Step 1: Write the failing test**

```python
# tests/cloud/test_hardening.py
import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient
from wingman.cloud import hardening

def test_rate_limiter_allows_then_blocks():
    rl = hardening.RateLimiter(max_per_min=2)
    assert rl.allow("k") is True
    assert rl.allow("k") is True
    assert rl.allow("k") is False
    assert rl.allow("other") is True  # separate bucket

def _app(**mw):
    async def ok(request):
        return PlainTextResponse("ok")
    app = Starlette(routes=[Route("/x", ok)])
    return app

def test_body_limit_413():
    app = _app()
    app.add_middleware(hardening.BodyLimitMiddleware, max_bytes=10)
    client = TestClient(app)
    r = client.post("/x", content=b"x" * 50, headers={"content-length": "50"})
    assert r.status_code == 413

def test_security_headers_present():
    app = _app()
    app.add_middleware(hardening.SecurityHeadersMiddleware)
    client = TestClient(app)
    r = client.get("/x")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "strict-transport-security" in r.headers

def test_rate_limit_429():
    app = _app()
    app.add_middleware(hardening.RateLimitMiddleware, limiter=hardening.RateLimiter(1))
    client = TestClient(app)
    assert client.get("/x").status_code == 200
    assert client.get("/x").status_code == 429
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/cloud/test_hardening.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# src/wingman/cloud/hardening.py
"""Transport hardening: body size cap, rate limiting, CORS, security headers."""
from __future__ import annotations

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import identity

log = logging.getLogger("wingman.cloud")


class RateLimiter:
    """Fixed-window-ish token bucket, per key, in-memory (single instance)."""
    def __init__(self, max_per_min: int):
        self._max = max_per_min
        self._buckets: dict[str, tuple[float, int]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        window_start, count = self._buckets.get(key, (now, 0))
        if now - window_start >= 60.0:
            window_start, count = now, 0
        if count >= self._max:
            self._buckets[key] = (window_start, count)
            return False
        self._buckets[key] = (window_start, count + 1)
        return True


class BodyLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_bytes: int):
        super().__init__(app)
        self._max = max_bytes

    async def dispatch(self, request: Request, call_next):
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > self._max:
            return JSONResponse({"error": "payload_too_large"}, status_code=413)
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return resp


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, limiter: RateLimiter):
        super().__init__(app)
        self._limiter = limiter

    async def dispatch(self, request: Request, call_next):
        try:
            key = identity.current_user_id()
        except identity.Unauthenticated:
            key = request.client.host if request.client else "anon"
        if not self._limiter.allow(key):
            log.warning("rate limit hit key=%s path=%s", key, request.url.path)
            return JSONResponse({"error": "rate_limited"}, status_code=429)
        return await call_next(request)


def apply(app, cfg) -> None:
    # Order matters: added last runs first. We want body limit and CORS outermost.
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RateLimitMiddleware, limiter=RateLimiter(60))
    app.add_middleware(BodyLimitMiddleware, max_bytes=cfg.max_body_bytes)
    if cfg.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cfg.allowed_origins,  # never "*"
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["authorization", "content-type"],
        )
```

In `server_http.build_app`, after `app.add_middleware(AuthMiddleware, ...)`, call `hardening.apply(app, cfg)`. Confirm middleware order gives: CORS -> body limit -> rate limit (after identity set) -> auth -> app. Because rate limit reads `identity`, it must run after `AuthMiddleware` sets it; with Starlette, middleware added later wraps outermost, so add `AuthMiddleware` before calling `hardening.apply` only if rate limiting should sit outside auth. For per-user limits, add the rate limiter so it runs just inside auth: add `AuthMiddleware` last. Adjust ordering and add an inline comment documenting the final chain.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/cloud/test_hardening.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/wingman/cloud/hardening.py src/wingman/cloud/server_http.py tests/cloud/test_hardening.py
git commit -m "feat(cloud): body cap, per-user rate limit, CORS allow-list, security headers"
```

---

## Task 10: Observability (optional Sentry + PostHog, env-gated, scrubbed)

**Files:**
- Create: `src/wingman/cloud/observability.py`
- Test: `tests/cloud/test_observability.py`

**Interfaces:**
- Produces:
  - `observability.init(cfg) -> None` (no-op if both DSN/key unset; never raises if libs missing).
  - `observability.scrub_event(event: dict) -> dict` (Sentry `before_send`): removes request body, `email`, token-like fields.
  - `observability.capture(event_name: str, user_id: str, props: dict | None = None) -> None` (PostHog; no-op if unset; props must not include content).

- [ ] **Step 1: Write the failing test**

```python
# tests/cloud/test_observability.py
from wingman.cloud import observability

def test_scrub_removes_pii():
    event = {"request": {"data": "secret task text"},
             "user": {"email": "a@x.com", "id": "u1"},
             "extra": {"authorization": "Bearer abc"}}
    out = observability.scrub_event(event)
    assert "data" not in out.get("request", {})
    assert "email" not in out.get("user", {})
    assert out["user"]["id"] == "u1"
    assert "authorization" not in out.get("extra", {})

def test_init_noop_without_config(monkeypatch):
    class Cfg: sentry_dsn = None; posthog_key = None
    observability.init(Cfg())  # must not raise

def test_capture_noop_without_key():
    # no PostHog configured -> silently no-op
    observability.capture("plan_created", "u1", {"count": 1})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/cloud/test_observability.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# src/wingman/cloud/observability.py
"""Optional, env-gated, cloud-only observability. The local product never
imports this. Never sends plan/task content, emails, or tokens."""
from __future__ import annotations

import logging

log = logging.getLogger("wingman.cloud")

_posthog = None
_SENSITIVE_KEYS = {"email", "authorization", "token", "access_token", "content", "data"}


def scrub_event(event: dict) -> dict:
    req = event.get("request")
    if isinstance(req, dict):
        req.pop("data", None)
        req.pop("cookies", None)
    user = event.get("user")
    if isinstance(user, dict):
        user.pop("email", None)
    extra = event.get("extra")
    if isinstance(extra, dict):
        for k in list(extra):
            if k.lower() in _SENSITIVE_KEYS:
                extra.pop(k, None)
    return event


def init(cfg) -> None:
    global _posthog
    if getattr(cfg, "sentry_dsn", None):
        try:
            import sentry_sdk
            sentry_sdk.init(dsn=cfg.sentry_dsn, before_send=lambda e, h: scrub_event(e),
                            send_default_pii=False)
        except Exception as exc:  # missing lib or bad dsn must not crash boot
            log.warning("sentry init skipped: %s", exc)
    if getattr(cfg, "posthog_key", None):
        try:
            import posthog
            posthog.project_api_key = cfg.posthog_key
            _posthog = posthog
        except Exception as exc:
            log.warning("posthog init skipped: %s", exc)


def capture(event_name: str, user_id: str, props: dict | None = None) -> None:
    if _posthog is None:
        return
    try:
        _posthog.capture(distinct_id=user_id, event=event_name, properties=props or {})
    except Exception as exc:
        log.warning("posthog capture failed: %s", exc)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/cloud/test_observability.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/wingman/cloud/observability.py tests/cloud/test_observability.py
git commit -m "feat(cloud): optional env-gated Sentry + PostHog with PII scrubbing"
```

---

## Task 11: Entry point, Dockerfile, env example, README, SQL packaging

**Files:**
- Create: `src/wingman/cloud/__main__.py`
- Create: `Dockerfile`
- Create: `.env.example`
- Modify: `README.md` (add a "Wingman Cloud (hosted)" section + telemetry clarification)
- Modify: `pyproject.toml` (ensure `migrations/*.sql` is packaged, or switch `store_pg` to `importlib.resources`)
- Test: `tests/cloud/test_entrypoint.py`

**Interfaces:**
- Produces: `cloud.__main__.main()` building config from env, initializing the pool, observability, verifier, and running uvicorn. A `build_from_env() -> Starlette` helper is unit-tested without binding a socket.

- [ ] **Step 1: Write the failing test**

```python
# tests/cloud/test_entrypoint.py
import pytest
from wingman.cloud import __main__ as entry

REQUIRED = {
    "DATABASE_URL": "postgresql://u:p@localhost/db",
    "WORKOS_API_KEY": "sk", "WORKOS_CLIENT_ID": "cid",
    "WINGMAN_BASE_URL": "https://w.example.com",
}

def test_build_from_env_returns_app(monkeypatch):
    for k, v in REQUIRED.items():
        monkeypatch.setenv(k, v)
    # do not actually connect: stub pool creation + init
    async def _fake_pool(dsn): return object()
    async def _fake_init(pool): return None
    monkeypatch.setattr(entry.store_pg, "create_pool", _fake_pool)
    monkeypatch.setattr(entry.store_pg, "init_db", _fake_init)
    app = entry.build_from_env(connect=False)
    assert app is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/cloud/test_entrypoint.py -v`
Expected: FAIL with `AttributeError: ... 'build_from_env'`

- [ ] **Step 3: Implement entry point + artifacts**

```python
# src/wingman/cloud/__main__.py
"""`python -m wingman.cloud` / `wingman-cloud` entry point."""
from __future__ import annotations

import asyncio
import logging

from . import store_pg, observability, auth as auth_mod, server_http
from .config_cloud import CloudConfig


def build_from_env(connect: bool = True):
    cfg = CloudConfig.from_env()
    observability.init(cfg)
    if connect:
        pool = asyncio.get_event_loop().run_until_complete(store_pg.create_pool(cfg.database_url))
        asyncio.get_event_loop().run_until_complete(store_pg.init_db(pool))
        store_pg.set_pool(pool)
    verifier = auth_mod.TokenVerifier(
        issuer=server_http._idp_issuer(cfg),
        audience=cfg.base_url,
        jwks_uri=f"{server_http._idp_issuer(cfg)}/.well-known/jwks.json",
    )
    app = server_http.build_app(cfg, verifier)
    server_http_hardening = getattr(server_http, "_apply_hardening", None)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    import os
    import uvicorn
    app = build_from_env(connect=True)
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))


if __name__ == "__main__":
    main()
```

```dockerfile
# Dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY migrations ./migrations
RUN pip install --no-cache-dir ".[cloud,observability]"
ENV PORT=8000
EXPOSE 8000
CMD ["wingman-cloud"]
```

```
# .env.example  (placeholders only, never commit real values)
DATABASE_URL=postgresql://USER:PASSWORD@HOST/db?sslmode=require
WORKOS_API_KEY=sk_example
WORKOS_CLIENT_ID=client_example
WORKOS_ISSUER=https://your-tenant.authkit.app
WINGMAN_BASE_URL=https://your-wingman.onrender.com
ALLOWED_ORIGINS=https://claude.ai,https://chatgpt.com
# optional
SENTRY_DSN=
POSTHOG_KEY=
MAX_PLANS_PER_USER=100
MAX_TASKS_PER_PLAN=500
MAX_BATCH_SIZE=50
MAX_BODY_BYTES=262144
PORT=8000
```

README: add a "Wingman Cloud (hosted)" section describing the hosted URL, that connecting is via the standard MCP connector OAuth flow, and a clear telemetry note: the local pip install remains zero-telemetry and makes no network calls; the hosted service has server-side analytics (Sentry/PostHog) and stores plans in Postgres.

SQL packaging: ensure `migrations/001_init.sql` is included in the built package. Either add to `pyproject.toml` (`[tool.setuptools] include-package-data = true` + a `MANIFEST.in` with `recursive-include migrations *.sql`, or move the SQL under `src/wingman/cloud/migrations/` and load via `importlib.resources.files`). If you move it, update `store_pg._MIGRATION` accordingly and re-run Task 2's test.

- [ ] **Step 4: Run test + full cloud suite + local suite**

Run:
```bash
pytest tests/cloud/test_entrypoint.py -v
pytest tests/cloud -v
pytest tests/ -q --ignore=tests/cloud
```
Expected: all PASS (local suite unchanged)

- [ ] **Step 5: Verify Docker build**

Run: `docker build -t wingman-cloud .`
Expected: build succeeds.

- [ ] **Step 6: Commit**

```bash
git add src/wingman/cloud/__main__.py Dockerfile .env.example README.md pyproject.toml
git commit -m "feat(cloud): entry point, Dockerfile, env example, README cloud section"
```

---

## Task 12: End-to-end smoke test over HTTP (identity propagation proof)

**Files:**
- Create: `tests/cloud/test_smoke_http.py`

**Interfaces:**
- Consumes everything. Proves: a signed token drives a full create -> add -> tick -> get round trip over the real ASGI app, scoped to one user, with identity propagating from middleware into an async tool. Also asserts tools/list parity and that a second user cannot see the first user's plan over HTTP.

This is the task that proves the contextvar propagation assumption from Task 8. If it fails because identity is lost inside the MCP handler, switch the tool helpers to read identity from the MCP request context (the fallback noted in Task 8) and re-run.

- [ ] **Step 1: Write the test**

```python
# tests/cloud/test_smoke_http.py
import time
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.testclient import TestClient
from wingman.cloud import server_http, auth, store_pg
from wingman.cloud.config_cloud import CloudConfig

pytestmark = pytest.mark.asyncio

ISSUER = "https://idp.test"
AUD = "https://wingman.test"

@pytest.fixture(scope="module")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)

def _token(key, sub, email="u@x.com", name="U"):
    return jwt.encode(
        {"sub": sub, "iss": ISSUER, "aud": AUD, "exp": int(time.time()) + 300,
         "email": email, "name": name},
        key, algorithm="RS256",
    )

class _Verifier(auth.TokenVerifier):
    def __init__(self, pub):
        super().__init__(ISSUER, AUD, "https://idp.test/jwks")
        self._pub = pub
    def _signing_key(self, token):
        return self._pub

def _cfg():
    return CloudConfig(database_url="x", workos_api_key="x", workos_client_id="x",
                       base_url=AUD, allowed_origins=["https://claude.ai"],
                       sentry_dsn=None, posthog_key=None, max_plans_per_user=100,
                       max_tasks_per_plan=500, max_batch_size=50, max_body_bytes=262144)

async def test_full_round_trip_and_isolation(pg_pool, key):
    app = server_http.build_app(_cfg(), _Verifier(key.public_key()))
    client = TestClient(app)
    h_a = {"Authorization": f"Bearer {_token(key, 'alice')}"}
    h_b = {"Authorization": f"Bearer {_token(key, 'bob')}"}

    # well-known is public
    assert client.get("/.well-known/oauth-protected-resource").status_code == 200
    # unauthenticated MCP call rejected
    assert client.post("/mcp", json={}).status_code in (401, 400)

    # Drive MCP tool calls via the streamable-http JSON-RPC endpoint. Use the
    # MCP test client helper if available; otherwise post JSON-RPC envelopes for
    # tools/call create_plan, add_task, tick_task, get_plan with h_a, asserting
    # each result is non-error and scoped to alice. Then with h_b call get_plan
    # for alice's plan name and assert an isError / not-found result.
    # (Fill in the JSON-RPC envelopes against the installed mcp version's
    #  streamable-http route; assert the create->add->tick->get sequence and
    #  that bob cannot read alice's plan.)
```

The exact JSON-RPC envelope shape depends on the installed `mcp` streamable-HTTP route. During implementation, use the SDK's client/session test utilities to issue `initialize` then `tools/call`. The assertions are fixed: alice's round trip succeeds and bob gets not-found for alice's plan.

- [ ] **Step 2: Run the smoke test**

Run: `pytest tests/cloud/test_smoke_http.py -v`
Expected: PASS with `WINGMAN_TEST_DSN` set. If identity does not propagate into the tool, apply the Task 8 fallback and re-run until green.

- [ ] **Step 3: Run the entire suite**

Run:
```bash
pytest tests/ -q
```
Expected: all cloud tests pass (or skip without DSN), local tests unchanged.

- [ ] **Step 4: Commit**

```bash
git add tests/cloud/test_smoke_http.py
git commit -m "test(cloud): end-to-end HTTP round trip + cross-user isolation smoke"
```

- [ ] **Step 5: Push the branch**

```bash
git push -u origin feat/wingman-cloud
```

---

## Post-implementation: deploy (manual, not a code task)

1. Create a Neon project; copy the pooled connection string into Render's `DATABASE_URL`.
2. Create a WorkOS AuthKit project; set `WORKOS_API_KEY`, `WORKOS_CLIENT_ID`, `WORKOS_ISSUER`; enable Dynamic Client Registration; add sign-in methods (Google, email).
3. Create a Render web service from the repo (Docker). Set all env vars from `.env.example`. Set `WINGMAN_BASE_URL` to the Render URL.
4. Add a free uptime monitor pinging `/healthz` every ~10 min to avoid cold starts.
5. Add the Render URL as a connector in Claude (and ChatGPT); complete the OAuth consent once; verify a plan created on one device appears on another.
6. When satisfied, open a PR `feat/wingman-cloud -> dev`. Tag `v0.3.0-cloud` only when promoting `dev -> main`.

---

## Self-review notes (author)

- Spec coverage: codebase model (Task 1, package layout), data model + isolation (Tasks 2-4), users table + metrics (Task 3), auth/OAuth (Tasks 6, 8), quotas (Tasks 3-4), rate limit/CORS/headers/body cap (Task 9), Postgres parameterization (Tasks 2-4), secrets/.env (Task 11), logging hygiene (Tasks 8-9 log auth/rate events, never content), observability (Task 10), deployment/Dockerfile/config (Task 11), testing strategy incl. two-user isolation and smoke (Tasks 3-4, 12). Panel greeting (spec section 7) and the migration tool are intentionally deferred (greeting is a minor follow-up; migration is a separate sub-project) and are NOT in this plan.
- Open verification points flagged inline for the implementer: exact local serializer names (`plan_to_dict`/`task_to_dict`/list payload), exact local LLM-visible tool set for `LLM_TOOL_NAMES`, the `mcp` streamable-HTTP app constructor and JSON-RPC envelope shape, SQL packaging path, and middleware ordering. Each has a concrete fallback.
