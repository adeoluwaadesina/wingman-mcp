# README funnel reorder (Approach A)

## Problem

Wingman is getting impressions and reactions on LinkedIn/IG posts but no
corresponding GitHub stars or new Wingman Cloud signups. Traffic from those
posts lands on the GitHub README, which currently leads with `pip install
wingman-mcp`, a JSON MCP-host config block, and a restart step before any
payoff is shown. That's a reasonable path for a developer already sold on
the tool, but it's too much friction for a cold visitor evaluating in
seconds. The zero-install Wingman Cloud connect path (paste one URL into a
connector) already exists but is buried under "New in v0.3," Install, and
Configure — around line 159 of the current README.

## Goal

Resequence the README so the lowest-friction path (Wingman Cloud, one URL,
no install) is the first thing a new visitor is offered, while keeping all
existing content intact for readers who want the local/self-hosted path.

## Change

Reorder README.md sections (content edits only, no deletions):

1. Hero — unchanged (tagline, badges, demo GIF).
2. **New:** a short "Get started in 10 seconds" callout directly under the
   hero. States the one-URL connect step and links down to the full Cloud
   connect instructions. This is the new primary CTA.
3. "What is Wingman?" — unchanged, stays as the pitch immediately after the
   hero/CTA.
4. **Moved up:** the "Wingman Cloud (hosted, sync across devices)" section,
   currently at line 159, moves to directly follow "What is Wingman?" as
   the second major section.
5. Remaining sections ("New in v0.3," screenshots, "How it works," "Use
   Wingman in ChatGPT," tool reference, architecture, security, vs.
   alternatives, roadmap, etc.) stay, resequenced to read as supporting
   detail after the two lead sections above.
6. **Moved down / reframed:** "Install" (`pip install`) and "Configure"
   (per-host JSON blocks) move below the Cloud section and are reframed
   with a lead-in like "Want it fully local and self-hosted instead?" so
   they read as the alternative path, not the default one.

No content is deleted. No new features, no code changes — this is a
markdown reorder plus one new short callout block.

## Out of scope

- A dedicated landing page for social traffic (Approach B — separate spec,
  next after this one).
- Any change to the dev.to draft posts.
- Any change to the actual Cloud connect flow, install process, or code.

## Success criteria

README's first two sections after the hero are the CTA callout and the
Cloud connect steps; `pip install` / JSON config no longer appear before
them. All existing content still present, just reordered.
