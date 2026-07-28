# README Funnel Reorder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resequence `README.md` so the zero-install Wingman Cloud connect path is the first thing a new visitor sees, instead of `pip install` + JSON config.

**Architecture:** This is a content-only markdown reorder of a single file (`README.md`). No code changes, no new files except the plan/spec docs already committed. Work happens in two tasks: (1) add the new CTA callout and move the Cloud section up, (2) move Install/Configure down and reframe their lead-in copy. Each task is independently reviewable as a diff of `README.md`.

**Tech Stack:** Markdown only.

## Global Constraints

- No content may be deleted; every existing section must still be present after the reorder (source: spec "Change," point 5-6).
- No code changes, no changes to the Cloud connect flow, install process, or dev.to drafts (source: spec "Out of scope").
- Final order after the hero must be: CTA callout → "What is Wingman?" → Wingman Cloud section → remaining sections (source: spec "Success criteria").
- `pip install` / "Configure" sections must appear after the Cloud section, reframed with a "want it local instead?" style lead-in (source: spec "Change," point 6).

---

### Task 1: Add CTA callout and move Wingman Cloud section up

**Files:**
- Modify: `README.md` (hero section ~line 1-20, "What is Wingman?" ~line 24-32, "Wingman Cloud" section currently at line 159-186)

**Interfaces:**
- N/A (markdown content only, no code interfaces)

- [ ] **Step 1: Insert the "Get started in 10 seconds" callout**

Insert this block immediately after the closing `</div>` of the hero (after line 20, before the `---` at line 22):

```markdown
---

> ### Get started in 10 seconds
>
> No install. In Claude or ChatGPT, open **Settings → Connectors → Add
> custom connector** and paste:
>
> ```text
> https://wingman-mcp.onrender.com/mcp
> ```
>
> Sign in, and you're connected. Full steps below in
> [Wingman Cloud](#wingman-cloud-hosted-sync-across-devices).

<br/>
```

**Step 2: Cut the Wingman Cloud section and reinsert it after "What is Wingman?"**

Cut this entire block (currently lines 159-187, from `## Wingman Cloud (hosted, sync across devices)` through the `<br/>` and `---` right before `## Use Wingman in ChatGPT`):

```markdown
## Wingman Cloud (hosted, sync across devices)

Wingman Cloud is the hosted version: your plans live in one place and sync across
every device and assistant - Claude desktop, web, and mobile, and ChatGPT - so a
plan you build on your laptop is right there on your phone.

**Connect it (one time):**

1. In Claude, open **Settings -> Connectors -> Add custom connector** (on ChatGPT,
   add it as a custom MCP connector).
2. Enter the server URL:

   ```text
   https://wingman-mcp.onrender.com/mcp
   ```

3. A browser window opens to sign in with Google or email. Approve it, and you are
   connected. You only do this once per device; you stay signed in afterward.

That's it - create a plan on one device and it shows up on the others. The
interactive panel renders where the host supports it (Claude desktop and ChatGPT
today), and the clean text view is used everywhere else.

> Wingman Cloud is in early hosted beta. The local `pip install wingman` stays
> fully supported and zero-telemetry; the hosted service adds accounts and
> cross-device sync (see Security & privacy below).

<br/>

---
```

Reinsert it immediately after the "What is Wingman?" section's closing `---` (currently at line 34), so the order becomes: hero → CTA callout → "What is Wingman?" → Wingman Cloud section → (rest of file, starting with "New in v0.3").

- [ ] **Step 3: Verify structure with a heading scan**

Run: `grep -n '^## ' README.md`
Expected: first three `##`/`>` headings after the hero are, in order: the "Get started in 10 seconds" callout, `## What is Wingman?`, `## Wingman Cloud (hosted, sync across devices)`, followed by `## New in v0.3` and the rest unchanged.

- [ ] **Step 4: Verify nothing was deleted**

Run: `git diff --stat README.md`
Expected: same file, no lines showing as net-deleted content (a reorder shows as balanced add/remove of the moved block, plus the new callout as pure addition). Read the full diff and confirm every original section title still appears somewhere in the new file.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: lead README with zero-install Cloud connect path"
```

---

### Task 2: Move Install/Configure down and reframe lead-in copy

**Files:**
- Modify: `README.md` (`## Install` and `## Configure` sections, now sitting between "New in v0.3" and the "Wingman Cloud" section's old position)

**Interfaces:**
- N/A (markdown content only)

- [ ] **Step 1: Cut the Install and Configure sections**

After Task 1, `## Install` and `## Configure` (originally lines 49-157) sit directly after `## New in v0.3` and before where "Use Wingman in ChatGPT" now follows. Cut both sections as one contiguous block, from `## Install` through the `<br/>`/`---` right before `## Use Wingman in ChatGPT`.

- [ ] **Step 2: Add a reframed lead-in and reinsert after "Use Wingman in ChatGPT"**

Prepend this lead-in line directly above the existing `## Install` heading, then reinsert the whole cut block (lead-in + Install + Configure) immediately after the "Use Wingman in ChatGPT" section's closing `<br/>`/`---`, before `## How it works`:

```markdown
## Want it fully local and self-hosted instead?

Wingman also ships as a local MCP server you install and run yourself - no
account, no network calls, plans stored in local SQLite on your machine.

## Install
```

(The rest of the existing `## Install` and `## Configure` content follows unchanged after this new heading and lead-in paragraph.)

- [ ] **Step 3: Verify final section order**

Run: `grep -n '^## ' README.md`
Expected order of top-level `##` headings: `What is Wingman?`, `Wingman Cloud (hosted, sync across devices)`, `New in v0.3`, `Use Wingman in ChatGPT`, `Want it fully local and self-hosted instead?`, `Install`, `Configure`, `How it works`, `Screenshots`, `Tool reference`, `Use as an agent skill`, `Architecture`, `Security & privacy`, `vs. alternatives`, `Known limitations in v0.3`, `Roadmap`, `Development troubleshooting`, `Contributing`, `License`. Every original heading is still present, with only Install/Configure relocated and one new heading added.

- [ ] **Step 4: Verify nothing was deleted**

Run: `git diff --stat README.md` and read the full `git diff README.md` for this commit. Confirm every line of original Install/Configure content (including both pip/pipx variants, all four host configs, and the troubleshooting note) is present, unchanged, just relocated.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: move local install/config below Cloud connect path"
```
