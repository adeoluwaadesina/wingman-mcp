"""One-time migration: copy local (SQLite) plans into Wingman Cloud (Postgres).

Run this LOCALLY, where your `plans.db` lives. It reads your local plans and
recreates them in the cloud under your account, preserving task order and
status. Existing cloud plans with the same name are skipped (never clobbered).

Usage:
    wingman-migrate --user-id <your-cloud-user-id> [--dsn <neon-dsn>] [--dry-run]
    wingman-migrate --email you@example.com [--dsn <neon-dsn>]

Your cloud user id is the value shown in the Neon `users` table. The DSN comes
from --dsn or the DATABASE_URL environment variable (your PROD Neon string).
"""
from __future__ import annotations

import argparse
import asyncio
import os

from ..storage import db as local_db
from . import store_pg


async def _resolve_user_id(pool, user_id: str | None, email: str | None) -> str:
    if user_id:
        return user_id
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT user_id FROM users WHERE email = $1", email)
    if row is None:
        raise SystemExit(
            f"no cloud user found with email {email!r}. Sign in to Wingman Cloud "
            "first, or pass --user-id with the id shown in Neon."
        )
    return row["user_id"]


async def _run(dsn: str, user_id: str | None, email: str | None, dry_run: bool) -> tuple[list[str], list[str]]:
    pool = await store_pg.create_pool(dsn)
    store_pg.set_pool(pool)
    try:
        uid = await _resolve_user_id(pool, user_id, email)
        await store_pg.upsert_user(uid, email, None)  # ensure the row exists

        local_plans = local_db.list_plans()
        existing = {p["name"] for p in await store_pg.list_plans(uid)}

        migrated: list[str] = []
        skipped: list[str] = []
        for lp in local_plans:
            name = lp["name"]
            if name in existing:
                skipped.append(name)
                continue
            if dry_run:
                migrated.append(name)
                continue
            plan = local_db.get_plan(name)
            # One transaction per plan (plan row + all tasks via executemany) so a
            # large import does not make a network round-trip per task. Status and
            # order are preserved; done tasks get a completed_at.
            async with pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(
                        "INSERT INTO plans (user_id, name) VALUES ($1, $2)", uid, name
                    )
                    if plan.tasks:
                        await conn.executemany(
                            "INSERT INTO tasks (user_id, plan_name, content, status, sort_order, completed_at) "
                            "VALUES ($1, $2, $3, $4, $5, CASE WHEN $4 = 'done' THEN now() ELSE NULL END)",
                            [(uid, name, t.content, t.status, idx) for idx, t in enumerate(plan.tasks)],
                        )
            migrated.append(name)
        return migrated, skipped
    finally:
        await pool.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Migrate local Wingman plans into Wingman Cloud.")
    ident = parser.add_mutually_exclusive_group(required=True)
    ident.add_argument("--user-id", help="Cloud user id (from the Neon users table).")
    ident.add_argument("--email", help="Cloud account email (must already exist in the users table).")
    parser.add_argument("--dsn", default=os.environ.get("DATABASE_URL"),
                        help="Neon connection string (or set DATABASE_URL).")
    parser.add_argument("--dry-run", action="store_true", help="Report what would migrate, write nothing.")
    args = parser.parse_args(argv)

    if not args.dsn:
        raise SystemExit("no DSN: pass --dsn or set DATABASE_URL to your Neon connection string.")

    migrated, skipped = asyncio.run(_run(args.dsn, args.user_id, args.email, args.dry_run))

    verb = "Would migrate" if args.dry_run else "Migrated"
    print(f"{verb} {len(migrated)} plan(s): {', '.join(migrated) or '(none)'}")
    if skipped:
        print(f"Skipped {len(skipped)} already in cloud: {', '.join(skipped)}")


if __name__ == "__main__":
    main()
