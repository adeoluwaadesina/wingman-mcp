# Wingman Privacy Policy

_Last updated: 2026-07-06_

Wingman is an open-source MCP server that gives AI assistants a persistent,
interactive plan and to-do panel. It comes in two editions, and they handle your
data very differently. This policy explains both.

## Summary

- **Local edition** (`pip install wingman-mcp`): everything stays on your own
  machine. Wingman never sends your plans or tasks to any server we control.
- **Hosted edition** (the cloud connector at `wingman-mcp.onrender.com`): stores
  your account identifier and the plans and tasks you create, so they sync across
  your devices. We do not sell your data and we do not use it to train any model.

---

## Local edition (self-hosted / pip)

When you run Wingman locally as a stdio MCP server, it stores your plans and tasks
in a local database on your own computer (under your OS application-data
directory). This data:

- **Never leaves your machine.** There is no network call to any Wingman-operated
  service.
- Is readable and deletable by you at any time (delete the local database file, or
  use the in-app "Delete plan" / "Clear all" actions).

The only parties that ever see this data are you and the AI assistant you have
connected Wingman to (for example, Claude). That assistant's own privacy policy
governs how it handles the conversation.

## Hosted edition (cloud connector)

If you connect to the hosted Wingman service, the following applies.

### What we collect

- **Account identity.** When you sign in, our authentication provider gives us a
  unique user identifier and, where available, your email address and display
  name. We use these solely to associate your plans with your account and to keep
  your data separate from other users'.
- **Your content.** The plans, tasks, task statuses, and ordering you create
  through Wingman.
- **Connection metadata.** The app you connect from (for example, Claude or
  ChatGPT), derived from your client's User-Agent. This is operational, tells us
  nothing about your conversations, and is used only to understand which clients
  Wingman is used with.

We do **not** collect the contents of your AI conversations. Wingman only receives
the specific plan and task actions you or the assistant invoke.

### How we use it

- To store your plans and sync them across every device where you connect Wingman.
- To operate, secure, and debug the service (for example, rate limiting and error
  monitoring).

We never sell your data, never share it for advertising, and never use it to train
machine-learning models.

### Where it is stored and who processes it

The hosted service runs on third-party infrastructure. These subprocessors may
handle your data strictly to provide the service:

- **Render** - application hosting.
- **Neon** - managed PostgreSQL database where your account and plans are stored.
- **WorkOS** - authentication (sign-in) and identity.
- **Sentry** and **PostHog** - error monitoring and product analytics, used only
  if enabled by the operator, and limited to operational, non-content telemetry.

Each user's rows are isolated by account identifier on every database query.

### Data retention

Your plans and tasks are kept until you delete them (via the panel's delete
actions) or request deletion of your account. On account deletion we remove your
stored plans, tasks, and account record.

### Your choices

- Delete individual plans or tasks at any time from the panel.
- Disconnect the connector at any time from your AI assistant's settings.
- Request full account and data deletion using the contact below.

---

## Children

Wingman is not directed to children under 13 and we do not knowingly collect data
from them.

## Changes to this policy

We may update this policy as Wingman evolves. Material changes will be reflected by
the "Last updated" date above and in the project's changelog.

## Contact

Questions or data requests: open an issue at
<https://github.com/adeoluwaadesina/wingman-mcp/issues>, or email
**adeoluwaadesina26@gmail.com**.
