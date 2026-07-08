/* Wingman iframe controller.
 *
 * Uses the MCP Apps SDK (@modelcontextprotocol/ext-apps, exposed here as the
 * global `WingmanMCP`). The panel resource is static and carries no plan data;
 * the plan name + initial state arrive via the render-data channel - the
 * `toolresult` notification carrying the calling tool's structuredContent.
 *
 *   1. new App({name, version})
 *   2. addEventListener("toolresult"/"toolinput", ...) to capture render data
 *   3. await app.connect()
 *   4. app.callServerTool({name, arguments}) for all _ui_* callbacks + polling
 *   5. app.sendMessage({role, content}) for Run-task / Build-from-chat
 */
(function () {
  "use strict";

  // ---------- Single-init guard ----------
  // The host may re-evaluate this script when new render data arrives (it
  // re-mounts the View). Without a guard that re-run creates a SECOND App +
  // connect() ("AppBridge received a second ui/initialize") and double-binds
  // every DOM handler - later clicks then hit a stale/disconnected App and
  // silently no-op, which is exactly the "rename changes text but doesn't
  // save" symptom. We bind everything exactly once per document. A genuinely
  // fresh iframe document gets a fresh `window` and boots normally.
  if (window.__WINGMAN_BOOTED__) {
    console.debug("[wingman] script re-evaluated; reusing existing App instance.");
    return;
  }
  window.__WINGMAN_BOOTED__ = true;

  const POLL_FAST_MS = 2500;
  const POLL_SLOW_MS = 10000;
  const IDLE_THRESHOLD_MS = 30000;
  const THEME_KEY = "wingman:theme";

  // Media queries drive interaction mode. Checked live (.matches) so rotation
  // or window resizing picks the right behavior without a reload.
  const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)");
  const COARSE = window.matchMedia("(pointer: coarse)");
  // The plan menu is an anchored dropdown on every device (the bottom-sheet
  // variant fought Claude mobile's content-sized iframe). This stays a no-match
  // stub so the leftover sheet code paths are inert.
  const SHEET_MQ = { matches: false, addEventListener() {}, removeEventListener() {} };
  // Row swipe gestures are disabled: the delete X is always visible on touch,
  // and swipe-in-a-scroll-list fought vertical scrolling. Flip to re-enable.
  const SWIPE_ENABLED = false;

  let lastChangeAt = Date.now();
  let currentPollMs = POLL_FAST_MS;
  let lastPolledSig = "";
  let cameFromPicker = false;
  // The last navigation the HOST asked for via render data (a plan name, or the
  // plans list). Claude fires render-data once per tool call; ChatGPT re-delivers
  // the widget's bound tool output repeatedly (on re-render/focus/theme changes).
  // We act on host render data only when this key CHANGES, so a re-delivered
  // stale output can't yank the user out of the view they navigated to in-panel.
  let lastHostNavKey = null;

  const stage = document.getElementById("stage");
  const $ = (sel) => stage.querySelector(sel);
  const $$ = (sel) => Array.from(stage.querySelectorAll(sel));
  const $role = (role) => stage.querySelector(`[data-role="${role}"]`);

  // The plan this panel is bound to. Discovered from render data at connect
  // time; updated on rename.
  let currentPlanName = stage.dataset.plan || null;
  let state = { plan: null };
  let app = null;
  let connected = false;

  function setStatus(msg) {
    $role("status").textContent = msg || "";
  }

  // ---------- Theme ----------
  function applyTheme() {
    const stored = localStorage.getItem(THEME_KEY) || "auto";
    document.documentElement.setAttribute("data-theme", stored);
  }
  function cycleTheme() {
    const order = ["auto", "light", "dark"];
    const current = localStorage.getItem(THEME_KEY) || "auto";
    const next = order[(order.indexOf(current) + 1) % order.length];
    localStorage.setItem(THEME_KEY, next);
    applyTheme();
  }
  applyTheme();

  // ---------- MCP bridge ----------
  // Unwrap a CallToolResult into our structured payload ({text, plan, ...}).
  function unwrap(result) {
    if (!result || typeof result !== "object") return null;
    if (result.structuredContent && typeof result.structuredContent === "object") {
      return result.structuredContent;
    }
    // Fallback: a host that only returns text content blocks.
    return result;
  }

  // While a mutating call (anything other than the read-only poll) is in
  // flight, we pause the polling tick so a poll using a soon-to-be-stale plan
  // name can't race a rename and clobber fresh state.
  let busy = 0;

  async function callTool(name, args) {
    if (!app || !connected) {
      console.warn("[wingman] app not connected; skipped tool:", name, args);
      setStatus("Not connected to host yet.");
      return null;
    }
    const isMutation = name !== "_ui_get_plan";
    if (isMutation) busy += 1;
    console.debug("[wingman] -> callServerTool", name, args);
    try {
      const result = await app.callServerTool({ name: name, arguments: args || {} });
      console.debug("[wingman] <- result", name, result);
      if (result && result.isError) {
        const msg = (result.content || [])
          .filter((c) => c && c.type === "text")
          .map((c) => c.text)
          .join(" ");
        console.error("[wingman] tool reported error", name, msg);
        setStatus("Error: " + (msg || "tool failed"));
        return null;
      }
      return unwrap(result);
    } catch (err) {
      console.error("[wingman] tool error", name, err);
      setStatus("Error: " + (err && err.message ? err.message : "tool failed"));
      return null;
    } finally {
      if (isMutation) busy = Math.max(0, busy - 1);
    }
  }

  async function sendChatMessage(text) {
    if (!app || typeof app.sendMessage !== "function") return false;
    try {
      const res = await app.sendMessage({
        role: "user",
        content: [{ type: "text", text: text }],
      });
      if (res && res.isError) {
        setStatus("Host rejected the message.");
        return false;
      }
      return true;
    } catch (err) {
      console.error("[wingman] sendMessage failed", err);
      return false;
    }
  }

  // ---------- Count-up tween ----------
  // The big readouts (done count, percent) tween between values instead of
  // snapping. One rAF loop per element; interrupted tweens restart from the
  // currently displayed value so rapid ticks stay smooth.
  const numTweens = new WeakMap();
  function setNumber(el, value) {
    const target = Math.round(Number(value) || 0);
    const from = parseInt(el.textContent, 10) || 0;
    if (numTweens.has(el)) cancelAnimationFrame(numTweens.get(el));
    if (REDUCED.matches || from === target || document.visibilityState !== "visible") {
      el.textContent = String(target);
      numTweens.delete(el);
      return;
    }
    const t0 = performance.now();
    const dur = 480;
    function frame(now) {
      const p = Math.min(1, (now - t0) / dur);
      const eased = 1 - Math.pow(1 - p, 4); // ease-out-quart
      el.textContent = String(Math.round(from + (target - from) * eased));
      if (p < 1) numTweens.set(el, requestAnimationFrame(frame));
      else numTweens.delete(el);
    }
    numTweens.set(el, requestAnimationFrame(frame));
  }

  // ---------- Render ----------
  function render(payload, fromPoll) {
    if (payload && Array.isArray(payload.plans) && !payload.plan) {
      renderPicker(payload.plans, fromPoll);
      return;
    }
    const plan = payload && payload.plan;
    if (!plan) return;
    // Switching from picker mode to plan view
    $role("picker").hidden = true;
    $role("add-form").removeAttribute("hidden");
    $role("back-link").hidden = !cameFromPicker;
    $role("menu-toggle").hidden = false;
    $role("theme-toggle").hidden = false;
    if (fromPoll) {
      const sig = JSON.stringify(plan);
      if (sig !== lastPolledSig) {
        lastChangeAt = Date.now();
        lastPolledSig = sig;
      }
    } else {
      lastPolledSig = JSON.stringify(plan);
      lastChangeAt = Date.now();
    }
    state.plan = plan;
    state.plans = null;
    currentPlanName = plan.name;
    stage.dataset.plan = plan.name;

    const titleEl = $role("title");
    titleEl.style.cursor = "";
    titleEl.style.pointerEvents = "";
    // contenteditable stays "false" until user clicks - existing click handler manages that.
    if (titleEl.getAttribute("contenteditable") !== "true") {
      titleEl.textContent = plan.name;
    }
    const c = plan.counts || {};
    const total = c.total || 0;
    const done = c.done || 0;
    const inProg = c.in_progress || 0;
    const pending = c.pending || 0;
    const pct = total === 0 ? 0 : Math.round((done / total) * 100);

    $role("subtitle").textContent =
      total + " task" + (total === 1 ? "" : "s") + " · " + done + " done";
    $role("status").textContent = total === 0 ? "" : pct + "% complete";

    // Empty-state visuals (feather icon, "Nothing here yet", the Build CTA, and
    // the "or type below" divider) render ONLY when the plan has zero tasks.
    // The add-task input is a permanent sibling and always renders.
    const tasks = plan.tasks || [];
    const isEmpty = tasks.length === 0;
    const progress = $role("progress");
    const empty = $role("empty");
    const list = $role("task-list");
    progress.hidden = isEmpty;
    list.hidden = isEmpty;
    empty.hidden = !isEmpty;

    if (!isEmpty) {
      setNumber($role("p-done"), done);
      $role("p-total").textContent = "of " + total;
      setNumber($role("p-pct"), pct);
      // The fill scales (compositor-only) instead of animating width; the
      // track draws one notch per task via --segs.
      $role("p-fill").style.transform = "scaleX(" + pct / 100 + ")";
      progress.style.setProperty("--segs", String(total));
      $role("pill-done").textContent = String(done);
      $role("pill-progress").textContent = String(inProg);
      $role("pill-pending").textContent = String(pending);
    }

    renderTasks(tasks);
    refreshMenuState();
  }

  function renderPicker(plans, fromPoll) {
    if (fromPoll) {
      const sig = JSON.stringify(plans);
      if (sig !== lastPolledSig) {
        lastChangeAt = Date.now();
        lastPolledSig = sig;
      }
    } else {
      lastPolledSig = JSON.stringify(plans);
      lastChangeAt = Date.now();
    }
    state.plans = plans;
    state.plan = null;

    $role("progress").hidden = true;
    $role("task-list").hidden = true;
    $role("empty").hidden = true;
    $role("add-form").setAttribute("hidden", "");
    $role("back-link").hidden = true;
    $role("picker").hidden = false;
    $role("menu-toggle").hidden = true;
    $role("theme-toggle").hidden = true;
    closeMenu();

    const titleEl = $role("title");
    titleEl.setAttribute("contenteditable", "false");
    titleEl.style.cursor = "default";
    titleEl.style.pointerEvents = "none";
    titleEl.textContent = "Your plans";
    $role("subtitle").textContent = plans.length + " plan" + (plans.length === 1 ? "" : "s");
    $role("status").textContent = "";

    const list = $role("picker-list");
    list.innerHTML = "";
    if (plans.length === 0) {
      const li = document.createElement("li");
      li.className = "picker-empty";
      li.textContent = "No plans yet. Ask Claude to create one.";
      list.appendChild(li);
      return;
    }
    for (const p of plans) {
      const li = document.createElement("li");
      li.className = "picker-row";
      li.innerHTML = `
        <span class="picker-name"></span>
        <span class="picker-meta"></span>
        <svg class="picker-chevron" viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
          <path d="M9 6l6 6-6 6" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/>
        </svg>
      `;
      li.querySelector(".picker-name").textContent = p.name;
      li.querySelector(".picker-meta").textContent = (p.done || 0) + "/" + (p.total || 0) + " done";
      li.addEventListener("click", () => openPlan(p.name));
      list.appendChild(li);
    }
  }

  async function openPlan(name) {
    cameFromPicker = true;
    currentPlanName = name;
    stage.dataset.plan = name;
    $role("picker").hidden = true;
    $role("add-form").removeAttribute("hidden");
    lastPolledSig = "";
    lastChangeAt = Date.now();
    currentPollMs = POLL_FAST_MS;
    const res = await callTool("_ui_get_plan", { plan_name: name });
    if (res) render(res);
    startPoll();
  }

  // ---------- Task list: reconciling renderer ----------
  // Rows are keyed by task id and REUSED across renders, so state changes
  // animate in place (strike/settle) instead of the whole list repainting.
  // Rows mid-exit are ignored by the reconciler and by reorder collection.
  let dragging = false; // true while Sortable owns the list

  const liveRows = () =>
    $$(".task-row").filter((el) => !el.classList.contains("row-exit"));

  function renderTasks(tasks) {
    if (dragging) return; // never fight an active drag; next poll settles it
    const list = $role("task-list");
    const byId = new Map();
    liveRows().forEach((el) => byId.set(el.dataset.taskId, el));

    let anchor = null; // last correctly-placed row
    const seen = new Set();
    for (const t of tasks) {
      const key = String(t.id);
      seen.add(key);
      let row = byId.get(key);
      if (row) {
        updateRow(row, t);
      } else {
        row = taskRow(t);
        if (!REDUCED.matches) {
          row.classList.add("row-enter");
          row.addEventListener("animationend", () => row.classList.remove("row-enter"), { once: true });
        }
      }
      const desired = anchor ? anchor.nextElementSibling : list.firstElementChild;
      if (row !== desired) list.insertBefore(row, desired);
      anchor = row;
    }
    byId.forEach((el, key) => {
      if (!seen.has(key)) removeRow(el);
    });
  }

  function updateRow(row, t) {
    row._task = t;
    const textEl = row.querySelector(".task-text");
    if (textEl.textContent !== t.content) textEl.textContent = t.content;
    const wasDone = row.classList.contains("is-done");
    row.classList.toggle("is-done", t.status === "done");
    row.classList.toggle("is-in-progress", t.status === "in_progress");
    const cb = row.querySelector(".checkbox");
    cb.setAttribute("aria-checked", String(t.status === "done"));
    if (!wasDone && t.status === "done") celebrateDone(row);
  }

  function celebrateDone(row) {
    if (REDUCED.matches) return;
    row.classList.remove("just-done");
    void row.offsetWidth; // restart the settle animation cleanly
    row.classList.add("just-done");
    setTimeout(() => row.classList.remove("just-done"), 550);
  }

  // Animate a deleted row out (height collapse + fade), then detach it.
  function removeRow(row) {
    if (row.classList.contains("row-exit")) return;
    closeSwipe(row);
    row.classList.add("row-exit");
    if (REDUCED.matches) {
      row.remove();
      return;
    }
    row.style.height = row.offsetHeight + "px";
    void row.offsetHeight; // commit the starting height
    row.style.height = "0px";
    setTimeout(() => row.remove(), 240);
  }

  // ---------- Task status actions (optimistic: animate first, sync after) ----------
  async function setTaskStatus(row, status) {
    const t = row._task;
    if (!t) return;
    updateRow(row, Object.assign({}, t, { status: status }));
    await callTool("_ui_update_status", {
      plan_name: currentPlanName,
      task_id: t.id,
      status: status,
    });
    await refresh();
  }

  function toggleDone(row) {
    const t = row._task;
    if (!t) return;
    return setTaskStatus(row, t.status === "done" ? "pending" : "done");
  }

  // pending -> in_progress -> pending; a done task re-opens straight into
  // in_progress ("back in flight"). The checkbox still owns done <-> pending.
  function toggleProgress(row) {
    const t = row._task;
    if (!t) return;
    return setTaskStatus(row, t.status === "in_progress" ? "pending" : "in_progress");
  }

  async function deleteTask(row) {
    const t = row._task;
    if (!t) return;
    removeRow(row); // optimistic exit animation while the tool call runs
    await callTool("_ui_delete_task", { plan_name: currentPlanName, task_id: t.id });
    await refresh();
  }

  function taskRow(t) {
    const li = document.createElement("li");
    li.className = "task-row";
    if (t.status === "done") li.classList.add("is-done");
    if (t.status === "in_progress") li.classList.add("is-in-progress");
    li.dataset.taskId = String(t.id);
    li._task = t;

    const pos = t.position || t.id;
    li.innerHTML = `
      <div class="swipe-under" aria-hidden="true">
        <span class="su-done">
          <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
            <path d="M5 12l4 4 10-10" stroke="currentColor" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </span>
        <button class="su-delete" tabindex="-1" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M4.5 6.5h15m-11 0V5a1.5 1.5 0 0 1 1.5-1.5h4A1.5 1.5 0 0 1 15.5 5v1.5m-9 0l1 12A1.8 1.8 0 0 0 9.3 20.5h5.4a1.8 1.8 0 0 0 1.8-1.7l1-12.3" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </button>
      </div>
      <div class="row-main">
        <span class="drag-handle" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
            <circle cx="9" cy="6" r="1.6" fill="currentColor"/><circle cx="15" cy="6" r="1.6" fill="currentColor"/>
            <circle cx="9" cy="12" r="1.6" fill="currentColor"/><circle cx="15" cy="12" r="1.6" fill="currentColor"/>
            <circle cx="9" cy="18" r="1.6" fill="currentColor"/><circle cx="15" cy="18" r="1.6" fill="currentColor"/>
          </svg>
        </span>
        <button class="checkbox" role="checkbox" aria-checked="${t.status === "done"}" aria-label="Toggle task ${pos} done">
          <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
            <path d="M5 12l4 4 10-10" stroke="currentColor" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </button>
        <span class="task-text"></span>
        <button class="row-btn delete" aria-label="Delete task ${pos}">
          <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/></svg>
        </button>
        <button class="row-btn prog" aria-label="Toggle task ${pos} in progress">
          <svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">
            <circle cx="12" cy="12" r="8.2" stroke="currentColor" stroke-width="1.8" fill="none"/>
            <path d="M12 8v4l2.8 2" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </button>
        <button class="row-btn run" aria-label="Run task ${pos}">
          <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path d="M7 5l12 7-12 7z" fill="currentColor"/></svg>
        </button>
      </div>
    `;
    li.querySelector(".task-text").textContent = t.content;

    li.querySelector(".checkbox").addEventListener("click", () => toggleDone(li));
    li.querySelector(".row-btn.prog").addEventListener("click", () => toggleProgress(li));
    li.querySelector(".row-btn.delete").addEventListener("click", () => deleteTask(li));
    li.querySelector(".su-delete").addEventListener("click", () => deleteTask(li));

    li.querySelector(".row-btn.run").addEventListener("click", async () => {
      const task = li._task || t;
      const res = await callTool("_ui_get_run_task_prompt", {
        plan_name: currentPlanName,
        task_id: task.id,
      });
      const text = res && (res.prompt || res.text);
      if (text) {
        const sent = await sendChatMessage(text);
        if (!sent) setStatus("This host can't auto-send messages.");
      }
      await refresh();
    });

    attachSwipe(li);
    return li;
  }

  // ---------- Swipe gestures (touch only) ----------
  // Right past threshold = toggle done. Left past threshold = snap open and
  // reveal delete. Direction-locked: vertical movement stays native scroll,
  // and the drag handle keeps its own gesture (Sortable owns it).
  let openSwipeRow = null;
  const SWIPE_OPEN_PX = 84;   // width of the revealed delete zone
  const SWIPE_DONE_PX = 72;   // rightward commit threshold
  const SWIPE_DEL_PX = 56;    // leftward snap-open threshold
  const SWIPE_LOCK_PX = 10;   // movement before we pick an axis

  function closeSwipe(row) {
    const main = row.querySelector(".row-main");
    if (main) main.style.transform = "";
    row.classList.remove("swipe-open", "swiping", "swipe-armed");
    delete row.dataset.swipe;
    if (openSwipeRow === row) openSwipeRow = null;
  }

  function attachSwipe(li) {
    if (!SWIPE_ENABLED) return;
    const main = li.querySelector(".row-main");
    let startX = 0, startY = 0, dx = 0, axis = null, base = 0;

    main.addEventListener("touchstart", (e) => {
      if (!COARSE.matches || e.touches.length !== 1) return;
      if (e.target.closest(".drag-handle")) return; // Sortable owns that gesture
      if (openSwipeRow && openSwipeRow !== li) closeSwipe(openSwipeRow);
      startX = e.touches[0].clientX;
      startY = e.touches[0].clientY;
      axis = null;
      dx = 0;
      base = li.classList.contains("swipe-open") ? -SWIPE_OPEN_PX : 0;
    }, { passive: true });

    main.addEventListener("touchmove", (e) => {
      if (!COARSE.matches || e.touches.length !== 1) return;
      if (e.target.closest(".drag-handle")) return;
      const mx = e.touches[0].clientX - startX;
      const my = e.touches[0].clientY - startY;
      if (axis === null) {
        if (Math.abs(mx) < SWIPE_LOCK_PX && Math.abs(my) < SWIPE_LOCK_PX) return;
        axis = Math.abs(mx) > Math.abs(my) ? "x" : "y";
        if (axis === "x") {
          li.classList.add("swiping");
          main.style.transition = "none";
        }
      }
      if (axis !== "x") return;
      e.preventDefault(); // we own this horizontal drag
      dx = base + mx;
      // Rubber-band past the useful range so the row never flies away.
      const capped = dx > 0
        ? Math.min(dx, SWIPE_DONE_PX + (dx - SWIPE_DONE_PX) * 0.25, SWIPE_DONE_PX + 26)
        : Math.max(dx, -SWIPE_OPEN_PX + (dx + SWIPE_OPEN_PX) * 0.25, -SWIPE_OPEN_PX - 26);
      main.style.transform = "translateX(" + capped + "px)";
      li.dataset.swipe = dx > 0 ? "right" : "left";
      li.classList.toggle("swipe-armed", dx > SWIPE_DONE_PX || dx < -SWIPE_DEL_PX);
    }, { passive: false });

    const settle = () => {
      if (axis !== "x") { axis = null; return; }
      main.style.transition = ""; // hand back to the CSS spring
      li.classList.remove("swiping", "swipe-armed");
      if (dx > SWIPE_DONE_PX) {
        closeSwipe(li);
        toggleDone(li);
      } else if (dx < -SWIPE_DEL_PX) {
        li.classList.add("swipe-open");
        li.dataset.swipe = "left";
        main.style.transform = "translateX(-" + SWIPE_OPEN_PX + "px)";
        openSwipeRow = li;
      } else {
        closeSwipe(li);
      }
      axis = null;
    };
    main.addEventListener("touchend", settle);
    main.addEventListener("touchcancel", settle);
  }

  // ---------- Menu ----------
  function refreshMenuState() {
    const clearCompletedBtn = $('.menu button[data-action="clear-completed"]');
    if (clearCompletedBtn) {
      const c = state.plan && state.plan.counts ? state.plan.counts : {};
      clearCompletedBtn.disabled = !c.done;
    }
  }
  // The document-level click-outside listener is attached only while the menu
  // is open, and torn down when it closes - no permanently-live listener.
  let outsideHandler = null;
  function isMenuOpen() {
    return !$role("menu").hasAttribute("hidden");
  }
  let menuCloseTimer = null;
  function openMenu() {
    const menu = $role("menu");
    const backdrop = $role("menu-backdrop");
    if (menuCloseTimer) { clearTimeout(menuCloseTimer); menuCloseTimer = null; }
    menu.classList.remove("menu-leaving");
    backdrop.classList.remove("menu-leaving");
    menu.style.transform = "";
    menu.removeAttribute("hidden");
    // The backdrop only paints in bottom-sheet mode (CSS gates it), so it is
    // safe to unhide unconditionally.
    backdrop.removeAttribute("hidden");
    if (!outsideHandler) {
      outsideHandler = (e) => {
        const trigger = $role("menu-toggle");
        if (!menu.contains(e.target) && e.target !== trigger && !trigger.contains(e.target)) {
          closeMenu();
        }
      };
      // Defer so the click that opened the menu doesn't immediately close it.
      setTimeout(() => document.addEventListener("click", outsideHandler), 0);
    }
  }
  function closeMenu() {
    const menu = $role("menu");
    const backdrop = $role("menu-backdrop");
    if (outsideHandler) {
      document.removeEventListener("click", outsideHandler);
      outsideHandler = null;
    }
    if (menu.hasAttribute("hidden")) {
      backdrop.setAttribute("hidden", "");
      return;
    }
    // Bottom-sheet mode slides out before hiding; everything else hides now.
    if (SHEET_MQ.matches && !REDUCED.matches && !menuCloseTimer) {
      menu.style.transform = "";
      menu.classList.add("menu-leaving");
      backdrop.classList.add("menu-leaving");
      menuCloseTimer = setTimeout(() => {
        menuCloseTimer = null;
        menu.classList.remove("menu-leaving");
        backdrop.classList.remove("menu-leaving");
        menu.setAttribute("hidden", "");
        backdrop.setAttribute("hidden", "");
      }, 210);
    } else if (!menuCloseTimer) {
      menu.setAttribute("hidden", "");
      backdrop.setAttribute("hidden", "");
    }
  }
  function toggleMenu() {
    if (isMenuOpen()) closeMenu();
    else openMenu();
  }
  // Toggle on trigger click - a second click of the trigger closes the menu.
  $role("menu-toggle").addEventListener("click", (e) => {
    e.stopPropagation();
    toggleMenu();
  });

  // Bottom sheet drag-to-dismiss: pull the sheet down past 70px to close it,
  // release earlier and it springs back. Only downward drags move it.
  (function attachSheetDrag() {
    const menu = $role("menu");
    let startY = 0, dy = 0, active = false;
    menu.addEventListener("touchstart", (e) => {
      if (!SHEET_MQ.matches || e.touches.length !== 1) return;
      startY = e.touches[0].clientY;
      dy = 0;
      active = true;
      menu.style.transition = "none";
    }, { passive: true });
    menu.addEventListener("touchmove", (e) => {
      if (!active) return;
      dy = Math.max(0, e.touches[0].clientY - startY);
      if (dy > 4) {
        e.preventDefault(); // a drag, not a tap: suppress button clicks
        menu.style.transform = "translateY(" + dy + "px)";
      }
    }, { passive: false });
    const release = () => {
      if (!active) return;
      active = false;
      menu.style.transition = ""; // CSS spring takes it from here
      menu.style.transform = "";
      if (dy > 70) closeMenu();
      dy = 0;
    };
    menu.addEventListener("touchend", release);
    menu.addEventListener("touchcancel", release);
  })();

  // Sandboxed iframes block confirm() - render an in-panel banner instead.
  function showInlineConfirm(message, onConfirm) {
    const existing = document.getElementById("wingman-confirm");
    if (existing) existing.remove();
    const bar = document.createElement("div");
    bar.id = "wingman-confirm";
    bar.className = "confirm-bar";
    bar.innerHTML = `
      <span class="confirm-msg"></span>
      <button class="confirm-yes">Yes</button>
      <button class="confirm-no">Cancel</button>
    `;
    bar.querySelector(".confirm-msg").textContent = message;
    bar.querySelector(".confirm-yes").addEventListener("click", () => {
      bar.remove();
      onConfirm();
    });
    bar.querySelector(".confirm-no").addEventListener("click", () => {
      bar.remove();
    });
    stage.insertBefore(bar, $role("add-form"));
  }

  // Export: markdown shown in-panel in a selectable field with a Copy button.
  function showExportSheet(md) {
    const existing = document.getElementById("wingman-export");
    if (existing) existing.remove();
    const wrap = document.createElement("div");
    wrap.id = "wingman-export";
    wrap.className = "export-sheet";
    wrap.innerHTML = `
      <div class="export-panel">
        <div class="export-head">
          <span class="export-title">Export as markdown</span>
          <button class="export-close" aria-label="Close">
            <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/></svg>
          </button>
        </div>
        <textarea class="export-text" readonly spellcheck="false"></textarea>
        <div class="export-actions">
          <button class="export-copy">Copy</button>
        </div>
      </div>
    `;
    const ta = wrap.querySelector(".export-text");
    ta.value = md;
    const close = () => wrap.remove();
    wrap.querySelector(".export-close").addEventListener("click", close);
    wrap.addEventListener("click", (e) => { if (e.target === wrap) close(); });
    const copyBtn = wrap.querySelector(".export-copy");
    copyBtn.addEventListener("click", () => {
      ta.focus();
      ta.select();
      let ok = false;
      try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
      copyBtn.textContent = ok ? "Copied ✓" : "Press Ctrl/Cmd+C";
      setTimeout(() => { copyBtn.textContent = "Copy"; }, 1800);
    });
    stage.appendChild(wrap);
    ta.focus();
    ta.select(); // pre-selected so a manual copy works even if execCommand is blocked
  }

  async function handleMenuAction(action) {
    closeMenu();
    try {
      if (action === "all-plans") {
        currentPlanName = null;
        stage.dataset.plan = "";
        cameFromPicker = false;
        stopPoll();
        $role("back-link").hidden = true;
        lastPolledSig = "";
        lastChangeAt = Date.now();
        currentPollMs = POLL_FAST_MS;
        const res = await callTool("_ui_list_plans", {});
        if (res) render(res);
        startPoll();
      } else if (action === "rename") {
        startTitleEdit();
      } else if (action === "clear-completed") {
        await callTool("_ui_clear_completed", { plan_name: currentPlanName });
        await refresh();
      } else if (action === "clear-all") {
        showInlineConfirm("Clear all tasks in this plan?", async () => {
          await callTool("_ui_clear_all", { plan_name: currentPlanName });
          await refresh();
        });
      } else if (action === "export") {
        // Show the markdown in-panel to copy. Sending it into the chat composer
        // trips the host's "content may contain malicious instructions" guard,
        // which alarms users; an in-panel copy avoids that and needs no
        // clipboard permission (execCommand + manual select fallback).
        const res = await callTool("_ui_export_markdown", { plan_name: currentPlanName });
        const md = res && (res.markdown || res.text);
        if (md) showExportSheet(md);
        else setStatus("Nothing to export");
      } else if (action === "delete-plan") {
        showInlineConfirm("Delete this entire plan? This cannot be undone.", async () => {
          const res = await callTool("_ui_delete_plan", { plan_name: currentPlanName });
          if (res !== null) handlePlanDeleted();
        });
      } else if (action === "build-from-chat") {
        const res = await callTool("_ui_get_build_from_chat_prompt", { plan_name: currentPlanName });
        const text = res && (res.prompt || res.text);
        if (text) {
          const sent = await sendChatMessage(text);
          if (!sent) setStatus("This host can't auto-send messages.");
        }
      }
    } catch (err) {
      console.error("[wingman] menu action failed", action, err);
      setStatus("Action failed - see console");
    }
  }

  // After the plan is gone, stop polling, clear state, and navigate to the
  // picker so the user sees their remaining plans immediately.
  function handlePlanDeleted() {
    stopPoll();
    state.plan = null;
    currentPlanName = null;
    stage.dataset.plan = "";
    cameFromPicker = false;
    $role("back-link").hidden = true;
    $role("menu-toggle").hidden = true;
    $role("theme-toggle").hidden = true;
    const titleEl = $role("title");
    titleEl.setAttribute("contenteditable", "false");
    titleEl.style.cursor = "default";
    titleEl.style.pointerEvents = "none";
    lastPolledSig = "";
    lastChangeAt = Date.now();
    currentPollMs = POLL_FAST_MS;
    callTool("_ui_list_plans", {}).then((res) => {
      if (res) render(res);
      startPoll();
    });
  }

  $$('.menu button').forEach((btn) => {
    btn.addEventListener("click", () => handleMenuAction(btn.dataset.action));
  });

  // ---------- Title editing ----------
  function startTitleEdit() {
    const el = $role("title");
    el.setAttribute("contenteditable", "true");
    el.focus();
    const range = document.createRange();
    range.selectNodeContents(el);
    range.collapse(false);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
  }
  $role("title").addEventListener("click", () => {
    if ($role("title").getAttribute("contenteditable") !== "true") startTitleEdit();
  });
  $role("title").addEventListener("keydown", async (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      await commitTitle();
    } else if (e.key === "Escape") {
      e.preventDefault();
      cancelTitleEdit();
    }
  });
  $role("title").addEventListener("blur", () => commitTitle());
  function cancelTitleEdit() {
    const el = $role("title");
    el.setAttribute("contenteditable", "false");
    el.textContent = currentPlanName || "";
  }
  async function commitTitle() {
    const el = $role("title");
    if (el.getAttribute("contenteditable") !== "true") return;
    el.setAttribute("contenteditable", "false");
    const newName = el.textContent.trim();
    const oldName = currentPlanName;
    if (!newName || newName === oldName) {
      el.textContent = oldName || "";
      return;
    }
    const res = await callTool("_ui_rename_plan", {
      current_name: oldName,
      new_name: newName,
    });
    if (res && res.plan) {
      currentPlanName = res.plan.name;
      stage.dataset.plan = res.plan.name;
    } else {
      el.textContent = oldName || "";
    }
    await refresh();
  }

  // ---------- Add form ----------
  const addInput = $role("add-input");
  const addSubmit = $role("add-submit");
  addInput.addEventListener("input", () => {
    addSubmit.hidden = addInput.value.trim().length === 0;
  });
  $role("add-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = addInput.value.trim();
    if (!text || !currentPlanName) return;
    addInput.value = "";
    addSubmit.hidden = true;
    await callTool("_ui_add_task", { plan_name: currentPlanName, content: text });
    await refresh();
  });

  // ---------- Build from chat ----------
  $$('.primary-btn[data-action="build-from-chat"]').forEach((btn) => {
    btn.addEventListener("click", async () => {
      const res = await callTool("_ui_get_build_from_chat_prompt", { plan_name: currentPlanName });
      const text = res && (res.prompt || res.text);
      if (text) {
        const sent = await sendChatMessage(text);
        if (!sent) setStatus("This host can't auto-send messages.");
      }
    });
  });

  // ---------- Theme toggle ----------
  $role("theme-toggle").addEventListener("click", cycleTheme);

  // ---------- Sortable / drag-and-drop ----------
  if (window.Sortable) {
    new window.Sortable($role("task-list"), {
      handle: ".drag-handle",
      animation: 160,
      ghostClass: "sortable-ghost",
      chosenClass: "sortable-chosen",
      // Touch screens: use Sortable's JS fallback drag. Native HTML5 drag-and-
      // drop is unreliable on mobile (jumpy, drops that don't register), which
      // is what showed up as "drag glitches and doesn't rearrange".
      forceFallback: COARSE.matches,
      fallbackTolerance: 4,
      // Touch: require a brief press before a drag starts so a quick vertical
      // swipe scrolls the page instead of being captured as a reorder.
      delay: COARSE.matches ? 160 : 0,
      delayOnTouchOnly: true,
      onStart: () => {
        dragging = true;
        // A row left open from a swipe carries an inline transform on .row-main;
        // clear every open swipe so the dragged row isn't visually offset.
        if (openSwipeRow) closeSwipe(openSwipeRow);
        liveRows().forEach((el) => {
          const m = el.querySelector(".row-main");
          if (m) { m.style.transform = ""; m.style.transition = ""; }
        });
      },
      onEnd: async () => {
        dragging = false;
        const ids = liveRows()
          .map((el) => Number(el.dataset.taskId))
          .filter((n) => Number.isFinite(n));
        // Reorder requires the complete set of ids exactly once; a partial or
        // duplicated list is rejected server-side and would just bounce back.
        // Only write when it is a clean, complete permutation.
        const expected = state.plan && state.plan.tasks ? state.plan.tasks.length : ids.length;
        if (ids.length === expected && new Set(ids).size === ids.length) {
          await callTool("_ui_reorder_tasks", { plan_name: currentPlanName, ordered_ids: ids });
        }
        await refresh();
      },
    });
  }

  // ---------- Back link (picker entry only) ----------
  $role("back-link").addEventListener("click", async () => {
    currentPlanName = null;
    stage.dataset.plan = "";
    cameFromPicker = false;
    stopPoll();
    $role("back-link").hidden = true;
    lastPolledSig = "";
    lastChangeAt = Date.now();
    currentPollMs = POLL_FAST_MS;
    const res = await callTool("_ui_list_plans", {});
    if (res) render(res);
    startPoll();
  });

  // ---------- Polling ----------
  async function refresh(fromPoll) {
    if (currentPlanName) {
      const res = await callTool("_ui_get_plan", { plan_name: currentPlanName });
      if (res) render(res, fromPoll);
    } else {
      const res = await callTool("_ui_list_plans", {});
      if (res) render(res, fromPoll);
    }
  }

  let pollTimer = null;
  function startPoll() {
    stopPoll();
    pollTimer = setInterval(() => {
      if (document.visibilityState !== "visible" || busy !== 0) return;
      const idleMs = Date.now() - lastChangeAt;
      const targetMs = idleMs > IDLE_THRESHOLD_MS ? POLL_SLOW_MS : POLL_FAST_MS;
      if (targetMs !== currentPollMs) {
        currentPollMs = targetMs;
        startPoll();
        return;
      }
      refresh(true);
    }, currentPollMs);
  }
  function stopPoll() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      refresh();
      startPoll();
    } else {
      stopPoll();
    }
  });

  // ---------- Render-data ingestion ----------
  // Pull the plan name from a tool's input arguments (toolinput) and the full
  // plan state from a tool's structuredContent (toolresult). Either path
  // bootstraps the panel; whichever arrives first wins.
  function ingestToolInput(params) {
    const args = params && params.arguments;
    if (args && typeof args.plan_name === "string") {
      const key = "plan:" + args.plan_name;
      // Re-delivery of the same host navigation (ChatGPT re-emits): ignore it so
      // it can't bounce the user back after they navigated away in-panel.
      if (key === lastHostNavKey) return;
      if (!currentPlanName) {
        lastHostNavKey = key;
        currentPlanName = args.plan_name;
        stage.dataset.plan = args.plan_name;
        refresh();
      }
    }
  }
  function ingestToolResult(params) {
    if (params && params.isError) return;
    const payload = unwrap(params);
    if (payload && payload.plan) {
      const key = "plan:" + payload.plan.name;
      // Ignore a re-delivered show_plan output. Without this, ChatGPT's repeated
      // re-emits force the panel back into this plan and clear cameFromPicker
      // (hiding the back link) every couple of seconds. A genuinely new host
      // navigation (different plan) has a different key and still applies.
      if (key === lastHostNavKey) return;
      lastHostNavKey = key;
      // Direct show_plan from Claude (not via picker click) - drop the back link.
      cameFromPicker = false;
      render(payload);
    } else if (payload && Array.isArray(payload.plans)) {
      if (lastHostNavKey === "list") return;
      lastHostNavKey = "list";
      currentPlanName = null;
      stage.dataset.plan = "";
      render(payload);
    } else if (!currentPlanName) {
      refresh();
    }
  }

  // ---------- Bootstrap ----------
  async function boot() {
    const SDK = window.WingmanMCP;
    if (!SDK || typeof SDK.App !== "function") {
      setStatus("MCP Apps host not detected.");
      console.error("[wingman] WingmanMCP.App unavailable - cannot connect.");
      return;
    }
    // Create + connect the App EXACTLY ONCE. The instance is stashed on window
    // so even a pathological re-import can't spawn a second initialize.
    app = new SDK.App({ name: "wingman", version: "0.3.0" });
    window.__WINGMAN_APP__ = app;

    // Register listeners BEFORE connect so we don't miss the initial render data.
    app.addEventListener("toolinput", ingestToolInput);
    app.addEventListener("toolresult", ingestToolResult);

    try {
      await app.connect();
      connected = true;
      console.debug("[wingman] connected to host.");
    } catch (err) {
      console.error("[wingman] connect failed", err);
      setStatus("Couldn't connect to host.");
      return;
    }

    // If render data already set the plan name, sync once more; otherwise the
    // toolinput/toolresult handlers will kick the first refresh.
    if (currentPlanName) await refresh();
    startPoll();
  }

  boot();
})();
