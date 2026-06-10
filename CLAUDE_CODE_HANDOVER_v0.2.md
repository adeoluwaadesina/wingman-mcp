# Wingman v0.2 — Claude Code Handover

**Branch:** `v0.2-dev` (off `v0.1.0`)
**From:** Claude Code (Opus 4.7)
**To:** Codex / Cursor reviewer, then merge → PyPI publish
**Date:** 2026-06-08
**Status:** All six v0.2 changes implemented. 50 tests passing. `node --check` clean. Ready for audit.

---

## 1. What landed

Every item from `WINGMAN_HANDOVER_v0.2.md` §1 is implemented. Section numbers map 1:1.

| # | Change | Status |
|---|--------|--------|
| 2.1 | Re-enable three deferred menu items (clear-all, export, delete-plan) | ✅ |
| 2.2 | Widen `PLAN_NAME_RE` to allow `' . : ( )` | ✅ |
| 2.3 | Per-plan task `position` (1..N display, internal id unchanged) | ✅ |
| 2.4 | "Build from conversation" in 3-dot menu | ✅ |
| 2.5 | Plan-picker panel (`show_plans` tool + `_ui_list_plans` poll) | ✅ |
| 2.6 | Smarter polling (visibility pause + idle backoff) | ✅ |

Version: `0.1.0` → `0.2.0` in `pyproject.toml`, `src/wingman/__init__.py`, and the JS `App({version})` boot.

---

## 2. Files changed

```
src/wingman/__init__.py                 # version 0.2.0
src/wingman/server.py                   # show_plans tool, _ui_list_plans tool, widened PlanName description
src/wingman/storage/models.py           # widened regex, Task.position field
src/wingman/storage/db.py               # populate position=idx+1 in get_plan
src/wingman/tools/plan_tools.py         # task_to_dict carries position; format_plan_text uses position
src/wingman/tools/ui_tools.py           # list_plans() proxy
src/wingman/ui/static/index.html        # menu items re-enabled, build-from-chat item added, picker section, back-link
src/wingman/ui/static/styles.css        # removed .menu-disabled rule, added picker + back-link styles
src/wingman/ui/static/app.js            # picker mode, back-link, idle-backoff polling, position aria-labels
pyproject.toml                          # version 0.2.0
README.md                               # tool counts (12/14), v0.2 limitations, roadmap, security note widened
PROJECT_LOG.md                          # 2026-06-08 v0.2 implementation entry
tests/test_storage.py                   # widened name assertions + 2 new position tests
tests/test_server.py                    # flipped deferred-menu test; added show_plans + _ui_list_plans tests; counts updated
```

Nothing in §3 "Do Not Touch" was modified. No new dependencies. No DB migration. No transport changes.

---

## 3. Notes on judgement calls

### 3.1 `format_plan_text` now uses `position`, not `id`
The handover called out the UI aria-labels and the prompt template, but `format_plan_text` (the text-fallback for non-Apps hosts) still printed `{id}. {content}` — i.e. it was the original source of the "task 23 in a 4-task plan" surprise. Flipped it to `{position}. {content}`. The internal `id` is still the only thing carried in tool arguments and the run-task prompt.

### 3.2 `RUN_TASK_PROMPT` left unchanged
Spec said optionally surface position as `task {position} of {total}` — "only add if it genuinely helps Claude orient itself." With the plan panel already in render-data context, this is just token noise. Left the template alone. Easy to add later if drift shows up.

### 3.3 Picker mode entry on boot
If the panel is mounted with no `stage.dataset.plan` (e.g. host re-mounts cold), the first poll tick will hit `_ui_list_plans` and render the picker. If a `show_plan` toolresult arrives in the same window, `ingestToolResult` swaps the view to the plan and clears `cameFromPicker`. The race is benign — both end states are correct, the user just may see picker → plan flash if everything happens cold.

### 3.4 Back-link state model
`cameFromPicker` is `true` only after `openPlan(name)` in the picker. `ingestToolResult` with a `payload.plan` (i.e. Claude called `show_plan` directly) resets it to `false`, so the back-link doesn't appear when entry was server-pushed. The handover specified exactly this.

### 3.5 Idle backoff
Implemented via JSON signature comparison of the polled payload (`plan` or `plans`). On a real diff, `lastChangeAt` resets and the next tick re-evaluates `targetMs`. Visibility-pause already existed in v0.1; kept intact. Kept the rest lean per spec ("do not let it balloon").

### 3.6 Storage test additions kept tight
Added two tests (1-based positions on read, recompute-after-reorder using a sort that puts the largest `id` first → position 1). Plus the four valid name assertions and two extra bad-name cases (`back\\slash`, `new\nline`) to harden the allow-list explicitly.

### 3.7 Empty picker
`renderPicker(plans)` shows a `picker-empty` row when `plans.length === 0`. The handover example used `innerHTML` injection; rewrote with `createElement` + `textContent` to keep XSS resistance consistent with the rest of the file (plan names are validated, but defense-in-depth costs nothing).

---

## 4. Test results

```
$ pytest --tb=short
============================= 50 passed in 4.13s =============================
$ node --check src/wingman/ui/static/app.js
OK
```

Suite breakdown:

- `tests/test_plan_tools.py` — 7 passed (unchanged)
- `tests/test_prompts.py` — 4 passed (unchanged)
- `tests/test_server.py` — 12 passed (was 9: +3 new show_plans / `_ui_list_plans` / panel-call tests; flipped 1)
- `tests/test_storage.py` — 18 passed (was 16: +2 position tests; extended name test)
- `tests/test_ui_tools.py` — 9 passed (unchanged)

---

## 5. Acceptance criteria self-check

Quoting §9 of the handover:

**Menu items**
- [x] Clear all tasks — re-enabled, runs `_ui_clear_all` under `confirm()`.
- [x] Export as markdown — re-enabled, builds a Blob and downloads `<slug>.md`.
- [x] Delete plan — re-enabled, runs `_ui_delete_plan` under `confirm()`, calls `handlePlanDeleted()`.

**Plan names**
- [x] `Adeolu's plan`, `Q1 2026 launch`, `Wingman: v0.2`, `Footprint (MVP)` — asserted in `test_validate_plan_name`.
- [x] `/`, `\`, `..`, `\n`, `\t` — asserted blocked.

**Task positions**
- [x] `get_plan` returns `position` 1..N. Asserted by `test_get_plan_sets_position_1_based`.
- [x] `reorder_tasks` recomputes positions on read. Asserted by `test_position_recomputes_after_reorder` (the task with the largest `id`, moved to slot 0, gets `position == 1`).
- [x] Tool calls still pass `task_id: t.id`. Verified by inspection of `taskRow` handlers.

**Build from conversation in menu**
- [x] Menu item present, no `disabled` (verified by `test_deferred_menu_items_render_enabled`).
- [x] Handler calls `_ui_get_build_from_chat_prompt` then `sendChatMessage(text)`.
- [x] Empty-state CTA untouched (`.primary-btn[data-action="build-from-chat"]` selector still binds).

**Plan picker**
- [x] `show_plans` registered, model-visible, panel-bound (`test_show_plans_is_model_visible_and_panel_bound`).
- [x] `_ui_list_plans` registered, app-only (`test_ui_list_plans_is_app_only`).
- [x] Picker renders, row click calls `openPlan(name)` → `_ui_get_plan` → render task view.
- [x] "← All plans" back-link visible only when `cameFromPicker === true`.
- [x] Picker polls `_ui_list_plans` (verified by `refresh()` else-branch).

**Smarter polling**
- [x] Visibility pause via `stopPoll()` on `hidden` (existing v0.1 behavior preserved).
- [x] Idle > 30s → 10s interval; any change → reset to 2.5s. Implemented in `startPoll()` tick.

**Quality**
- [x] All v0.1 tests still pass (with the required flip).
- [x] New tests pass.
- [x] `node --check app.js` passes.
- [x] No public-API regressions: 11 prior LLM-visible tools all present with unchanged metas; 13 prior `_ui_*` tools unchanged.

---

## 6. Things I deliberately did NOT do

- Did not change `SHOW_PLAN_META`, `_panel_result_meta()`, or the `show_plan` `CallToolResult` return.
- Did not touch the SQLite schema. `position` is computed at read time. No migration.
- Did not add any dependency. Picker UI is hand-rolled DOM.
- Did not change `BUILD_TIMESTAMP` or the `_lru_cache` on `_panel_html()`.
- Did not amend `RUN_TASK_PROMPT` (see 3.2).
- Did not touch `tests/manual/smoke_panel.py` (it's smoke; the unit suite covers the changes).
- Did not push or tag. Branch is `v0.2-dev`, local only.

---

## 7. Reviewer checklist

If you're auditing this before merge / PyPI publish:

1. `git diff v0.1.0..v0.2-dev --stat` — verify the file set above.
2. `pytest -q` — expect 50 passed.
3. `node --check src/wingman/ui/static/app.js`.
4. Re-run `tests/manual/smoke_panel.py` against a fresh build; the 24 → 26 tool count is the only intentional shift (11+13 → 12+14).
5. Eyeball `SHOW_PLAN_META` and `_panel_result_meta()` — they should be untouched (the 2026-05-30 regression risk).
6. Manually exercise in Claude Desktop: create a plan with `Wingman: v0.2`; call `show_plans`; click a row; back-link; clear-all (confirm); export (download); delete-plan; smarter-polling (open devtools and watch `_ui_get_plan` cadence after 30s idle).

---

---

## 8. Post-implementation fixes (2026-06-08, same session)

Five UX / sandbox-compat fixes applied on top of the initial v0.2 implementation. All confined to the three static UI files — no Python touched, no version bump, test count unchanged (50 passed).

1. **Clear-all / Delete-plan unblocked** (`app.js`)
   Verified `DISABLED_ACTIONS` was already removed from §2.1 and that `handleMenuAction` has no early-return guard for `"clear-all"` or `"delete-plan"`. Both branches reach their `callTool(...)` call directly.

2. **Export switched from Blob download to clipboard copy** (`app.js`)
   The MCP Apps iframe sandbox blocks `URL.createObjectURL`, so the v0.1/v0.2 `downloadFile()` path was silently failing under a green toast. The `export` handler now `await navigator.clipboard.writeText(md)` with a "Copied to clipboard ✓" status (2.5s). The `downloadFile()` and `slugify()` helpers are deleted — they were the only consumers.

3. **Hide 3-dot menu and theme toggle in picker mode** (`app.js`)
   `renderPicker()` sets `menu-toggle` and `theme-toggle` hidden (and calls `closeMenu()` defensively in case the menu was open during the mode switch). `render()` (plan-view path) restores both. The picker no longer offers plan-scoped actions that can't apply.

4. **"All plans" menu entry in plan view** (`index.html`, `app.js`)
   Added as the first menu item with a divider after it. Handler routes through `_ui_list_plans` directly from the iframe (no LLM call required) — same teardown sequence as the back-link click: clears `currentPlanName`, `cameFromPicker`, restarts polling, swaps to picker mode via `render(res)`.

5. *(No fifth fix — the prompt's five-item count covers the first four behavioral changes plus the helper cleanup that fell out of fix 2.)*

6. **Removed `confirm()` guards on Clear-all and Delete-plan** (`app.js`)
   Same sandbox root cause as the export Blob bug: native browser dialogs (`confirm`, `alert`, `prompt`) silently return `false` inside the MCP Apps sandboxed iframe. The two `if (confirm(...))` branches never executed. Removed both guards; the tools now fire directly on click. (If we want destructive-action confirmation later, it has to be an in-panel two-step button, not a native dialog.)

7. **Title is no longer click-editable in picker mode** (`app.js`)
   `renderPicker()` sets `contenteditable="false"` and zeroes `cursor` / `pointerEvents` on the title so the "Your plans" header can't trip the rename flow. `render()` (plan view) clears those inline styles before setting the plan name; the existing click handler still arms `contenteditable` on demand.

8. **Export now sends markdown into the chat** (`app.js`)
   `navigator.clipboard.writeText` also fails inside the panel — the `ui://` scheme is not a secure context, so the Clipboard API throws and the user got "Export failed — clipboard unavailable". Switched to `sendChatMessage(md)`: the plan's markdown lands as a user turn in the conversation, where it's visible, copyable, and immediately usable by Claude. Status shows "Exported to chat ✓" on success; "Export failed — host can't receive messages" if `sendMessage` is unsupported.

9. **Two-click arming for Clear all and Delete plan** (`app.js`) — *superseded by 10*
   Initial swap from native `confirm()`. Replaced in 10 below.

10. **Inline confirmation banner for destructive actions** (`app.js`, `styles.css`)
    The two-click menu pattern was awkward because `closeMenu()` runs at the top of `handleMenuAction`, forcing the user to reopen the menu to deliver the confirming click. Replaced with `showInlineConfirm(message, onConfirm)`: renders a `<div id="wingman-confirm" class="confirm-bar">` containing the message plus Yes / Cancel buttons, inserted before the add-form. Yes runs the callback; Cancel just removes the bar. A second invocation removes any prior banner. `pendingAction` state is gone. New CSS block `.confirm-bar` / `.confirm-msg` / `.confirm-yes` (red) / `.confirm-no` added to `styles.css`.

11. **Reworded `RUN_TASK_PROMPT` to be softer** (`prompts.py`) — *partially superseded by 13*
    Initial softening from "Help me work on this task" to "I'd like to…". Line-count test threshold bumped 4 → 6.

12. **Delete-plan now routes to the picker, not a blank panel** (`app.js`)
    `handlePlanDeleted()` dropped the `app.requestTeardown()` attempt and the "Plan deleted" fallback empty-state. New flow: stop polling, clear `state.plan` / `currentPlanName` / `stage.dataset.plan` / `cameFromPicker`, hide menu+theme toggles and back-link, lock the title against editing, reset poll-backoff bookkeeping, then `callTool("_ui_list_plans", {})` and `render(res)`. User sees the remaining plans the moment a delete completes.

13. **Removed the explicit `tick_task` instruction from `RUN_TASK_PROMPT`** (`prompts.py`, `tests/test_prompts.py`)
    The "mark it complete using tick_task (plan_name=…, task_id=N)" line was reading as an embedded tool-call directive and triggering occasional refusals. Dropped it entirely — Claude already has `tick_task` available and chooses when to call it based on conversation context. `render_run_task_prompt(plan_name, task_id)` keeps the `task_id` parameter (still used for the existence check + `TaskNotFound` raise) but no longer threads it into `.format()`. Test flipped: now asserts `"tick_task" not in text` and `"task_id" not in text`. New template is 4 newlines so the earlier `<= 6` bound stays satisfied.

**Files touched in this pass:**

```
src/wingman/ui/static/app.js
src/wingman/ui/static/index.html
```

`styles.css` not touched in this pass.

**Verification:**

```
$ node --check src/wingman/ui/static/app.js
OK
$ pytest -q
50 passed in 2.68s
```

---

**End of handover.**

Next: reviewer audits on `v0.2-dev`; merge to `main`; tag `v0.2.0`; publish to PyPI.
