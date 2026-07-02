/* Wingman iframe controller.
 *
 * Uses the MCP Apps SDK (@modelcontextprotocol/ext-apps, exposed here as the
 * global `WingmanMCP`). The panel resource is static and carries no plan data;
 * the plan name + initial state arrive via the render-data channel — the
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
  // every DOM handler — later clicks then hit a stale/disconnected App and
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

  let lastChangeAt = Date.now();
  let currentPollMs = POLL_FAST_MS;
  let lastPolledSig = "";
  let cameFromPicker = false;

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
    // contenteditable stays "false" until user clicks — existing click handler manages that.
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
      $role("p-done").textContent = String(done);
      $role("p-total").textContent = "of " + total;
      $role("p-pct").textContent = String(pct);
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

  function renderTasks(tasks) {
    const list = $role("task-list");
    list.innerHTML = "";
    for (const t of tasks) {
      list.appendChild(taskRow(t));
    }
  }

  function taskRow(t) {
    const li = document.createElement("li");
    li.className = "task-row";
    if (t.status === "done") li.classList.add("is-done");
    if (t.status === "in_progress") li.classList.add("is-in-progress");
    li.dataset.taskId = String(t.id);

    const pos = t.position || t.id;
    li.innerHTML = `
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
      <button class="row-btn run" aria-label="Run task ${pos}">
        <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path d="M7 5l12 7-12 7z" fill="currentColor"/></svg>
      </button>
    `;
    li.querySelector(".task-text").textContent = t.content;

    li.querySelector(".checkbox").addEventListener("click", async () => {
      const next = t.status === "done" ? "pending" : "done";
      await callTool("_ui_update_status", {
        plan_name: currentPlanName,
        task_id: t.id,
        status: next,
      });
      await refresh();
    });

    li.querySelector(".row-btn.delete").addEventListener("click", async () => {
      await callTool("_ui_delete_task", { plan_name: currentPlanName, task_id: t.id });
      await refresh();
    });

    li.querySelector(".row-btn.run").addEventListener("click", async () => {
      const res = await callTool("_ui_get_run_task_prompt", {
        plan_name: currentPlanName,
        task_id: t.id,
      });
      const text = res && (res.prompt || res.text);
      if (text) {
        const sent = await sendChatMessage(text);
        if (!sent) setStatus("This host can't auto-send messages.");
      }
      await refresh();
    });

    return li;
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
  // is open, and torn down when it closes — no permanently-live listener.
  let outsideHandler = null;
  function isMenuOpen() {
    return !$role("menu").hasAttribute("hidden");
  }
  function openMenu() {
    $role("menu").removeAttribute("hidden");
    if (!outsideHandler) {
      outsideHandler = (e) => {
        const menu = $role("menu");
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
    $role("menu").setAttribute("hidden", "");
    if (outsideHandler) {
      document.removeEventListener("click", outsideHandler);
      outsideHandler = null;
    }
  }
  function toggleMenu() {
    if (isMenuOpen()) closeMenu();
    else openMenu();
  }
  // Toggle on trigger click — a second click of ⋯ closes the menu.
  $role("menu-toggle").addEventListener("click", (e) => {
    e.stopPropagation();
    toggleMenu();
  });

  // Sandboxed iframes block confirm() — render an in-panel banner instead.
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
        // Clipboard is unavailable (ui:// is not a secure context) and Blob
        // downloads are sandbox-blocked. Send the markdown as a chat message
        // instead — visible, copyable, no permissions needed.
        const res = await callTool("_ui_export_markdown", { plan_name: currentPlanName });
        const md = res && (res.markdown || res.text);
        if (md) {
          const sent = await sendChatMessage(md);
          if (sent) {
            setStatus("Exported to chat ✓");
            setTimeout(() => setStatus(""), 2500);
          } else {
            setStatus("Export failed — host can't receive messages");
          }
        } else {
          setStatus("Nothing to export");
        }
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
      setStatus("Action failed — see console");
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
      onEnd: async () => {
        const ids = $$('.task-row').map((el) => Number(el.dataset.taskId));
        await callTool("_ui_reorder_tasks", { plan_name: currentPlanName, ordered_ids: ids });
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
    if (args && typeof args.plan_name === "string" && !currentPlanName) {
      currentPlanName = args.plan_name;
      stage.dataset.plan = args.plan_name;
      refresh();
    }
  }
  function ingestToolResult(params) {
    if (params && params.isError) return;
    const payload = unwrap(params);
    if (payload && payload.plan) {
      // Direct show_plan from Claude (not via picker click) — drop the back link.
      cameFromPicker = false;
      render(payload);
    } else if (payload && Array.isArray(payload.plans)) {
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
      console.error("[wingman] WingmanMCP.App unavailable — cannot connect.");
      return;
    }
    // Create + connect the App EXACTLY ONCE. The instance is stashed on window
    // so even a pathological re-import can't spawn a second initialize.
    app = new SDK.App({ name: "wingman", version: "0.2.0" });
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
