# Wingman landing page (Approach B)

## Problem

LinkedIn/IG posts about Wingman get impressions and reactions but no
click-through conversion (no new GitHub stars, no new Wingman Cloud
signups). A prior session shipped Approach A (reordering the README's
funnel) but then reverted it at the user's request in favor of building a
dedicated landing page instead — a page purpose-built for cold social
traffic, rather than repurposing the developer-facing README.

## Goal

A single-page, static marketing site for Wingman that a cold visitor from
LinkedIn/IG can land on, understand the product in seconds, and convert
by either connecting Wingman Cloud (primary) or visiting the GitHub repo
(secondary) — without needing to read technical setup docs first.

## Visual identity

Match the existing product's "flight-ops instrument" brand: night-navy
backgrounds, signal-orange accent, mono font for data/technical text, sans
font for prose. Exact tokens (from `src/wingman/ui/static/styles.css`,
dark theme):

- Background: `#151a24` (surface), `#1c2230` (surface-2), `#242c3c` (surface-3)
- Accent (signal-orange): `#ff6a2b`, ink-on-accent `#17100b`, soft accent wash `rgba(255,106,43,0.13)`
- Text: `#edeff4` (primary), `#9aa2b4` (muted), `#626b7e` (faint)
- Status colors: done green `#3ddc7a`, in-progress blue `#5b9bff`, danger red `#ff5d55`
- Border: `rgba(237,239,244,0.08)` normal, `rgba(237,239,244,0.16)` strong
- Font: system sans for prose, `ui-monospace` stack for data/technical readouts (URLs, counters, code)

Existing brand assets to reuse (in `docs/assets/`): `wingman-icon.png`,
`wingman-demo.gif`, `connect-3steps.gif`, panel screenshots
(`panel-populated.png`, `panel-in-progress.png`, `panel-mobile.png`,
`panel-populated-light.png`, `panel-menu.png`, `panel-export.png`).

## Page structure

### 1. Hero

Two-column layout (copy left, product mockup right), validated in the
visual companion as "Concept 2 — Live Panel":

- Headline: "The plan panel your AI actually keeps." (subheadline variations
  allowed at implementation time, but this is the validated direction)
- Subhead: one sentence — a persistent, interactive to-do panel inside
  Claude or ChatGPT; survives restarts, syncs across devices
- Primary CTA: **"Connect Wingman Cloud"** button. On click, expands in
  place (no page navigation, no scroll-jump) into a card containing:
  - A Claude / ChatGPT toggle (the connect steps differ slightly by host)
  - A URL bar showing `https://wingman-mcp.onrender.com/mcp` with a working
    copy-to-clipboard button
  - A numbered 3-step walkthrough under the toggle's selected host (open
    Settings → Connectors → Add custom connector; paste the URL; sign in
    with Google or email — one-time per device)
- Secondary CTA: "View on GitHub" linking to
  `https://github.com/adeoluwaadesina/wingman-mcp`
- Right column: a CSS-built mockup of the live product panel (task rows,
  status rail, progress readout) — not a screenshot, matches the validated
  "Live Panel" concept's visual language

### 2. How it works

A short section (3 steps or one looping GIF) using existing assets
(`connect-3steps.gif` and/or `wingman-demo.gif`) to show the product in
motion rather than re-explain it in text.

### 3. Feature highlights

Exactly 4 highlights, no more (deliberately curated subset of the
README's full feature list, not a reproduction of it):
- Persists across conversation restarts
- Syncs across every device via Wingman Cloud
- Works in both Claude and ChatGPT
- Click checkboxes yourself, or let the AI tick them as it finishes work

### 4. Repeat CTA

The same connect card pattern as the hero (URL + copy button + 3-step
walkthrough), presented in a simplified/compact form at the bottom of the
page, so a visitor who scrolled past the hero without converting gets a
second chance without scrolling back up.

### 5. Footer

Links: GitHub repo, MIT license, and a link back to the full README (for
visitors who want the complete technical docs, tool reference, etc. — the
landing page does not attempt to replace the README, only to convert cold
traffic that would otherwise bounce off it).

## Explicitly out of scope

- No FAQ section, no pricing section, no comparison/"vs alternatives" table
  — this page is deliberately short, optimized for fast conversion rather
  than exhaustive information (confirmed with the user).
- No changes to the README, the dev.to drafts, the actual Cloud connect
  flow, or any product code.
- No JS framework — plain HTML/CSS(+minimal vanilla JS for the
  expand-in-place card and copy-to-clipboard) is sufficient for a static
  one-pager.

## Hosting

GitHub Pages, deployed from this same repository (confirmed with the
user). Exact mechanism (branch vs `docs/` folder vs Pages-from-Actions)
and directory layout to be decided at planning time.

## Success criteria

- A visitor can understand what Wingman does and connect Wingman Cloud
  without leaving the landing page or reading the README.
- The page renders the validated hero (Concept 2 layout, expand-in-place
  connect card) and all 5 structural sections above.
- Deployed and reachable via GitHub Pages.
