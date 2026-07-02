# Wingman - Connect Walkthrough + Asset Map

Everything you need to (a) write the "how to connect" section and (b) capture the
right frames from your screen recording of the explainer.

## The hosted connect URL

```
https://wingman-mcp.onrender.com/mcp
```

One URL, every host. You sign in once per device with Google or email and stay
signed in. Your plans live in the cloud (Neon Postgres) and sync across devices.

---

## Connect, per host

### Claude (desktop + web + mobile)
1. Open **Settings -> Connectors -> Add custom connector**.
2. Paste the URL above.
3. A browser window opens. Sign in with Google or email and approve.
4. Done. Ask Claude to "show my Wingman plans" and the panel renders inline.

> Mobile: once you have connected on any device, Claude mobile is already
> connected (the account is what syncs, not the device). Just open Claude on your
> phone and your plans are there.

### ChatGPT
1. Add it as a **custom MCP connector** and paste the same URL.
2. Sign in when prompted.
3. Tools work. The interactive panel renders where the host supports MCP Apps;
   elsewhere you get the clean text view.

### Claude Code (CLI)
```bash
claude mcp add --transport http wingman-cloud https://wingman-mcp.onrender.com/mcp
```
Then complete the browser sign-in it prompts for.

### Prefer local-only? (no account, zero network)
```bash
pipx install wingman-mcp
```
Then add `{"mcpServers": {"wingman": {"command": "wingman"}}}` to your host config.
Same panel, plans stored in local SQLite, nothing leaves your machine.

---

## Troubleshooting (put the top two in the README)

- **"Couldn't reach Wingman" right after a deploy, or panel shows a stale look:**
  reconnect the connector (remove + re-add) or close and reopen the panel. This
  clears the cached panel and re-reads the server identity.
- **First request after the service has been idle is slow:** the hosted service
  is on a free tier that sleeps when idle; the first call wakes it (a few
  seconds), then it is fast.
- **Panel shows a "W" instead of the Wingman mark:** the server advertises its
  icon correctly; whether it renders next to tool calls depends on the host
  client's support for MCP server icons. The plans still work either way.

---

## Asset map - which recorded frame goes where

Record the explainer playing full-screen (or the panel live in Claude), then pull
these stills / short clips. Drop them into `docs/assets/` with these exact names so
the README picks them up:

| File in `docs/assets/`      | What to capture                                                        | Used in README            |
| --------------------------- | ---------------------------------------------------------------------- | ------------------------- |
| `wingman-demo.gif`          | The whole explainer, or a 6-10s loop of tasks checking off + sync      | Top hero                  |
| `panel-populated.png`       | The panel with the "Launch Wingman" plan, a few tasks, progress tape   | Screenshots: populated    |
| `panel-in-progress.png`     | A task mid-flight (in-progress beacon) + a couple done                 | Screenshots: live status  |
| `panel-menu.png`            | The 3-dot dropdown open (Rename, Export, Delete plan, etc.)            | Screenshots: menu         |
| `panel-export.png`          | The in-panel Export-as-markdown copy sheet                             | Screenshots: export       |
| `panel-mobile.png`          | The panel on a phone (bounded header, tasks, visible delete X)         | Screenshots: mobile       |
| `connect-3steps.gif`        | Scene 5 of the explainer (the 3 connect steps + URL)                   | Cloud connect section     |

Tips:
- For GIFs, keep them under ~5 MB so GitHub renders them inline. Record at the
  panel's native size, trim tight, and export at 12-15 fps.
- Dark background reads best for the flight-ops look. The explainer is already on
  deep navy; for live-panel shots, use Claude's dark theme.
- The panel's own footer shows `WINGMAN v0.3.0 Cloud` - a nice detail to keep in
  frame for the populated shot.
