-- migrations/001_init.sql
-- Initial schema for Wingman Cloud (multi-tenant).
-- All tables use IF NOT EXISTS so this file is safe to apply repeatedly.

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
