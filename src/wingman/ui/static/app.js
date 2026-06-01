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

  const POLL_MS = 2500;
  const THEME_KEY = "wingman:theme";

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
  function render(payload) {
    const plan = payload && payload.plan;
    if (!plan) return;
    state.plan = plan;
    currentPlanName = plan.name;
    stage.dataset.plan = plan.name;

    if ($role("title").getAttribute("contenteditable") !== "true") {
      $role("title").textContent = plan.name;
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
      $role("p-fill").style.width = pct + "%";
      $role("pill-done").textContent = String(done);
      $role("pill-progress").textContent = String(inProg);
      $role("pill-pending").textContent = String(pending);
    }

    renderTasks(tasks);
    refreshMenuState();
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

    li.innerHTML = `
      <span class="drag-handle" aria-hidden="true">&#x2807;&#x2807;</span>
      <button class="checkbox" role="checkbox" aria-checked="${t.status === "done"}" aria-label="Toggle done">
        <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
          <path d="M5 12l4 4 10-10" stroke="currentColor" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </button>
      <span class="task-text"></span>
      <button class="row-btn delete" aria-label="Delete task">
        <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/></svg>
      </button>
      <button class="row-btn run" aria-label="Run this task">
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

  // Trigger a browser download of text as a file.
  function downloadFile(filename, text, mime) {
    try {
      const blob = new Blob([text], { type: mime || "text/markdown" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      return true;
    } catch (err) {
      console.error("[wingman] download failed", err);
      return false;
    }
  }

  function slugify(name) {
    return (name || "plan").replace(/[^a-z0-9_-]+/gi, "-").replace(/^-+|-+$/g, "") || "plan";
  }

  // Deferred to v0.2 — disabled in the menu markup. Defense in depth: even if a
  // click leaks past pointer-events:none, never invoke the underlying tool.
  const DISABLED_ACTIONS = new Set(["clear-all", "export", "delete-plan"]);

  async function handleMenuAction(action) {
    if (DISABLED_ACTIONS.has(action)) {
      console.debug("[wingman] menu action deferred to v0.2:", action);
      return;
    }
    closeMenu();
    try {
      if (action === "rename") {
        startTitleEdit();
      } else if (action === "clear-completed") {
        await callTool("_ui_clear_completed", { plan_name: currentPlanName });
        await refresh();
      } else if (action === "clear-all") {
        if (confirm("Clear all tasks in this plan?")) {
          await callTool("_ui_clear_all", { plan_name: currentPlanName });
          await refresh();
        }
      } else if (action === "export") {
        const res = await callTool("_ui_export_markdown", { plan_name: currentPlanName });
        const md = res && (res.markdown || res.text);
        if (md) {
          const ok = downloadFile(slugify(currentPlanName) + ".md", md, "text/markdown");
          setStatus(ok ? "Exported markdown" : "Export failed — see console");
          if (ok) setTimeout(() => setStatus(""), 2000);
        } else {
          setStatus("Nothing to export");
        }
      } else if (action === "delete-plan") {
        if (confirm("Delete this entire plan? This cannot be undone.")) {
          const res = await callTool("_ui_delete_plan", { plan_name: currentPlanName });
          if (res !== null) {
            handlePlanDeleted();
          }
        }
      }
    } catch (err) {
      console.error("[wingman] menu action failed", action, err);
      setStatus("Action failed — see console");
    }
  }

  // After the plan is gone, stop polling, clear state, and ask the host to
  // tear down the View. If teardown isn't supported, fall back to a message.
  function handlePlanDeleted() {
    stopPoll();
    state.plan = null;
    currentPlanName = null;
    setStatus("Plan deleted.");
    let requested = false;
    if (app && typeof app.requestTeardown === "function") {
      try {
        app.requestTeardown();
        requested = true;
      } catch (err) {
        console.error("[wingman] requestTeardown failed", err);
      }
    }
    if (!requested) {
      // No teardown channel — blank the panel body so it doesn't look live.
      $role("progress").hidden = true;
      $role("task-list").hidden = true;
      const empty = $role("empty");
      empty.hidden = false;
      $role("title").textContent = "Plan deleted";
      $role("subtitle").textContent = "";
    }
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

  // ---------- Polling ----------
  async function refresh() {
    if (!currentPlanName) return;
    const res = await callTool("_ui_get_plan", { plan_name: currentPlanName });
    if (res) render(res);
  }

  let pollTimer = null;
  function startPoll() {
    stopPoll();
    pollTimer = setInterval(() => {
      if (document.visibilityState === "visible" && busy === 0) refresh();
    }, POLL_MS);
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
      render(payload);
    } else if (!currentPlanName) {
      // No structuredContent.plan but maybe we learned the name elsewhere.
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
    app = new SDK.App({ name: "wingman", version: "0.1.0" });
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
