# Wingman Cloud Foundation - Design Spec

Date: 2026-06-30
Branch target: `feat/wingman-cloud` off `dev`
Status: approved design, pre-implementation

## 1. Purpose and scope

Turn Wingman from a single-user local stdio MCP server into a hosted,
multi-tenant MCP server reachable over the public internet, so a user's plans
and tasks sync across every device and LLM client (Claude mobile/web/desktop,
ChatGPT) backed by one database.

This spec covers the **Cloud Foundation only**. The following are explicitly
out of scope and get their own spec -> plan -> build cycles later, each
depending on this foundation:

- **Wingman Wrapped** (yearly Spotify-Wrapped-style recap, emailed + inline panel).
- **Cross-plan linking** (plans referencing other plans).
- **Claude Connectors Directory submission** (needs the deployed live service).
- **Local-to-cloud migration tool** (one-time export local plans -> import to
  cloud). Deferred to its own small follow-up by user decision.

### Success criteria

1. A user authenticates once per client and can create/read/update plans and
   tasks over HTTPS from any MCP client.
2. Two users can never see, modify, or even detect each other's data, including
   by guessing plan names or numeric task ids (no IDOR).
3. The shipped local pip product (stdio + SQLite) is byte-for-byte unchanged and
   keeps passing its existing tests.
4. The hosted service runs on Render free tier with Neon Postgres, behind a
   managed identity provider, with the full 2026-06-29 security threat model
   satisfied.

## 2. Codebase model: one repo, parallel cloud module

Decision: **one repo, parallel `cloud/` module** (not a storage abstraction, not
a fork). Rationale: the shipped local product is clean and tested; an abstracted
storage interface would have to span sync SQLite and async Postgres, which is
awkward and risks regressing the live product. The cloud persistence functions
genuinely differ (user_id filtering, composite PK, Postgres placeholders, async),
so a parallel module is less code and lower risk than forcing one interface.

```
src/wingman/
  models.py          SHARED, untouched   (Plan, Task, validate_plan_name)
  prompts.py         SHARED, untouched
  tools/             rendering helpers (format_plan_text) reused by cloud
  storage/db.py      LOCAL ONLY, untouched (SQLite, sync, stdio)
  server.py          LOCAL ONLY, untouched (stdio entry point)
  cloud/             NEW - all cloud code lives here
    __init__.py
    __main__.py      `python -m wingman.cloud` entry point
    config_cloud.py  env-driven config (DSN, IdP keys, quotas) - no hardcoded secrets
    store_pg.py      the persistence functions, Postgres + async + user_id
    identity.py      reads user_id from the authenticated request context
    auth.py          validates IdP token, serves OAuth resource-server metadata
    server_http.py   FastMCP streamable-HTTP entry + middleware (auth, rate limit, CORS)
    observability.py optional Sentry + PostHog wiring, env-gated, cloud-only
```

Entry points:
- `wingman` / `python -m wingman` -> local stdio (unchanged).
- `wingman-cloud` / `python -m wingman.cloud` -> hosted HTTP.

Principles:
- The local product imports nothing from `cloud/`. Zero regression risk.
- Cloud exposes the **same 13 LLM-visible tools** with identical names and
  semantics, so every client sees an identical Wingman. Only transport and
  storage differ.
- Async all the way on the cloud side (FastMCP streamable-HTTP is async ->
  `asyncpg` driver to Neon).

## 3. Data model and multi-tenancy

The security-critical core (threat model item 1: tenant isolation / IDOR).
Postgres schema on Neon:

```sql
CREATE TABLE users (
    user_id      TEXT PRIMARY KEY,      -- the IdP `sub` claim (stable, opaque)
    email        TEXT,                  -- attribute, not the key; for Wrapped + contact
    display_name TEXT,
    created_at   TIMESTAMPTZ DEFAULT now(),
    last_seen_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE plans (
    user_id     TEXT NOT NULL REFERENCES users(user_id),
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (user_id, name)          -- composite: two users CAN share a name
);

CREATE TABLE tasks (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id      TEXT NOT NULL,          -- denormalized for fast ownership checks
    plan_name    TEXT NOT NULL,
    content      TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending','in_progress','done','blocked')),
    sort_order   INTEGER NOT NULL,
    created_at   TIMESTAMPTZ DEFAULT now(),
    updated_at   TIMESTAMPTZ DEFAULT now(),
    completed_at TIMESTAMPTZ NULL,
    FOREIGN KEY (user_id, plan_name) REFERENCES plans(user_id, name) ON DELETE CASCADE
);

CREATE INDEX idx_tasks_plan ON tasks(user_id, plan_name, sort_order);
```

### Isolation rules (enforced in `store_pg.py`)

1. **`user_id` in every WHERE clause.** No query reads or writes a row without
   `WHERE user_id = $1`. `delete_plan("Roadmap")` can only touch the caller's
   own "Roadmap".
2. **`user_id` comes from the session, never the client.** It is a function
   argument inside `store_pg.py`, filled by `identity.user_id()` reading the
   authenticated contextvar. It is never an MCP tool parameter. The tool
   signatures the model sees are unchanged (`tick_task(plan_name, task_id)`).
3. **Task-level ops verify ownership** via `WHERE id = $1 AND user_id = $2`
   (and `AND plan_name = $3` where relevant). A client guessing another user's
   numeric task id gets "not found" because user_id will not match. This is why
   user_id is denormalized onto `tasks` - ownership is checkable on the row
   directly without a join per call.
4. **Quotas enforced here** (item 3): see Security section for numbers.

### users upsert

On every authenticated request, upsert the `users` row: set `last_seen_at`,
fill `email` / `display_name` from the verified token claims. This yields native
metrics (user counts, signups/week, active users via `last_seen_at`) and seeds
the Wingman Wrapped mailing list with no extra plumbing.

### Rename transaction

The rename (INSERT new -> UPDATE children -> DELETE old) stays a single
transaction, now keyed by user_id, using `asyncpg`'s transaction context, so it
keeps the atomicity the local version has.

## 4. Authentication

Wingman Cloud is an **OAuth 2.1 resource server**. It runs no login screen and
stores no passwords. It only answers: is this token valid, and whose is it.

### Provider

**WorkOS AuthKit** (recommended). It explicitly supports the MCP connector OAuth
flow including **Dynamic Client Registration** (clients like Claude register
themselves rather than needing a hand-created client id per app), and has a high
free MAU ceiling. Runner-up: **Stytch** (also markets MCP "Connected Apps").
`auth.py` isolates the provider behind a small interface so swapping is contained.

### Flow (first connect of a client)

1. User adds Wingman's URL as a connector in Claude / ChatGPT.
2. Client hits Wingman, gets `401` plus OAuth metadata
   (`/.well-known/oauth-protected-resource`) pointing at WorkOS.
3. Client registers with WorkOS (Dynamic Client Registration) and opens the
   WorkOS-hosted login in a browser / in-app web view. User signs in once
   (Google / email / etc). This is the standard connector consent handoff, not
   an inline form rendered in the chat.
4. WorkOS issues an access token to the client.
5. Every Wingman call carries that token. Wingman validates it (signature
   against WorkOS JWKS, `exp`, `aud`/issuer) and reads the stable `sub` claim ->
   that is `user_id`.

After the first time, tokens refresh silently. Sign-in is once per device/client,
not per conversation. Which sign-in methods appear is WorkOS config.

### What Wingman implements (`auth.py` + middleware)

- Serves OAuth resource-server discovery metadata for the MCP remote-auth
  handshake.
- Validates the JWT on **every** request; reject -> `401`, no plan/task tool
  runs (item 2: no anonymous access).
- Derives `user_id` from the verified `sub` claim, sets it in a contextvar.
  Nothing downstream trusts any client-supplied identity.
- Caches WorkOS JWKS in memory with refresh.

### Identity model

`user_id = sub claim` (stable, opaque). Email/display_name are stored as
attributes on `users`, read from token claims, never used as the key.

## 5. Security hardening (maps 2026-06-29 threat model items 1-8)

Items 1 (isolation) and 2 (auth) are covered above. The rest, made concrete:

### Resource quotas (item 3) - in `store_pg.py`, returned as clean isError

| Limit | Default | Env override |
|---|---|---|
| Plans per user | 100 | `MAX_PLANS_PER_USER` |
| Tasks per plan | 500 | `MAX_TASKS_PER_PLAN` |
| Batch size per add_tasks | 50 | `MAX_BATCH_SIZE` |
| Task content length | 2000 chars | (matches local) |
| Plan name length | 64 chars | (matches local) |
| HTTP request body size | 256 KB | `MAX_BODY_BYTES` |

### Rate limiting (item 4)

Per-user_id and per-IP token bucket in middleware, set above the 2.5s panel poll
cadence so a legit panel never trips it (e.g. 60 req/min/user sustained with a
short burst allowance). Return `429` on breach; log the event.

### Transport hardening (item 5)

- TLS only (Render terminates HTTPS); reject non-HTTPS.
- Strict CORS allow-list from `ALLOWED_ORIGINS` (known Claude/ChatGPT origins),
  never `*`.
- Security headers: HSTS, `X-Content-Type-Options: nosniff`, restrictive CSP on
  the panel HTML.
- Errors stay `isError` over HTTP; no Python tracebacks reach the client.

### Postgres safety (item 6)

Every query parameterized via asyncpg (`$1`, `$2`), never f-strings or `%` into
SQL. Rename stays one transaction.

### Secrets (item 7)

Neon DSN, WorkOS client secret, signing keys via Render env vars / secret store,
never committed. Add `.env.example` with placeholders to document required vars.
`.gitignore` already excludes `*.db` and internal docs.

### Logging hygiene (item 8)

Never log task/plan content, emails, or tokens. Do log auth failures,
rate-limit hits, and quota rejections (user_id + IP) for abuse detection. Cloud
uses `datetime.now(timezone.utc)` (fixes the local `datetime.utcnow()`
deprecation nit; local file itself untouched, cloud just does it right).

## 6. Observability (optional, cloud-only, env-gated)

Both off unless their env var is set; the local code path never imports them.
The README's "no telemetry" promise is about the **local pip product** and stays
true; the README will state explicitly that the **hosted** service has
server-side analytics.

- **Sentry** (`SENTRY_DSN`) - server-side exceptions + performance. A
  `before_send` scrubber strips plan/task content, emails, and tokens before
  anything is sent, preserving item 8.
- **PostHog** (`POSTHOG_KEY`) - server-side product analytics. Emits event names
  + user_id + counts only (`plan_created`, `task_ticked`, `user_signed_up`),
  never content. Powers DAU/MAU, retention, funnels for the visa metrics story.

Where the operator looks:

| To see | Look in |
|---|---|
| Plans/tasks/users data | Neon console or any Postgres client |
| App logs, crashes, deploys | Render dashboard |
| Exceptions with stack traces | Sentry |
| Usage / retention / user counts | PostHog |

## 7. Panel enhancement (minor, cloud-only)

The cloud-served panel knows who the user is, so it can greet them: a small
header touch such as "Adeolu's plans" or a name/avatar in the corner, sourced
from `display_name`. Local panel stays anonymous. Minor, additive, not a
Foundation blocker.

## 8. Deployment and configuration

Runtime: plain Dockerfile (12-factor) so it runs on Render now and Railway/Fly
later with no code change. `server_http.py` launches FastMCP streamable-HTTP
bound to `$PORT`.

Config via environment variables (`config_cloud.py` reads these; `.env.example`
documents them with placeholders):

```
DATABASE_URL          Neon Postgres DSN
WORKOS_API_KEY        IdP
WORKOS_CLIENT_ID
WINGMAN_BASE_URL      public HTTPS URL, for OAuth metadata
ALLOWED_ORIGINS       CORS allow-list (Claude/ChatGPT origins)
SENTRY_DSN            optional, off if unset
POSTHOG_KEY           optional, off if unset
MAX_PLANS_PER_USER    optional quota override
MAX_TASKS_PER_PLAN    optional
MAX_BATCH_SIZE        optional
MAX_BODY_BYTES        optional
```

Deploy flow: push to `dev` -> (later) merge to `main` -> Render auto-deploys ->
Render provides HTTPS + URL -> set that URL as `WINGMAN_BASE_URL` and as a
connector in Claude/ChatGPT. Neon provisioned once; DSN in `DATABASE_URL`. A
free keep-warm uptime ping avoids cold starts.

Schema management: a small `migrations/` with the initial DDL applied on first
boot (idempotent `CREATE TABLE IF NOT EXISTS`), matching how local `init_db()`
already works. No heavy migration framework for Foundation.

Hosting note: Render free tier, $0, no credit card, cannot surprise-bill. Sleeps
when idle (cold start on first hit after idle), mitigated by the keep-warm ping;
750 instance-hours/month covers a warm always-on service. Switching to Railway
(~$5/mo, always-on) later is a deploy change only, no code change.

## 9. Branching and process

All work on `feat/wingman-cloud` off `dev`. Nothing touches `main` until it is an
official release. `dev` is the working branch; `main` is reserved for final
builds. Cloud first release tagged `v0.3.0-cloud`.

## 10. Testing strategy

- **Local regression:** existing local suite keeps passing unchanged (proves
  zero regression to the shipped product).
- **store_pg unit tests:** against a disposable Postgres (Neon branch or local
  container). Cover: every function filters by user_id; two-user isolation (user
  B cannot read/modify/delete user A's plan or task by name or by guessed id);
  composite PK lets two users share a plan name; quota rejections; rename
  atomicity.
- **Auth tests:** invalid/expired/missing token -> 401; valid token -> user_id
  derived from sub; no tool runs unauthenticated.
- **Middleware tests:** rate-limit 429, body-size cap, CORS rejection of a
  disallowed origin.
- **isError discipline:** forced errors return clean isError results, never
  tracebacks, over HTTP.
- **Smoke:** spawn `wingman-cloud`, drive it over HTTP with a test token, assert
  tools/list parity with local (same 13 LLM-visible names) and a full
  create -> add -> tick -> show round trip scoped to one user.
```
