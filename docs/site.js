const CLOUD_URL = "https://wingman-mcp.onrender.com/mcp";

const STEPS = {
  claude: [
    "Open <b>Settings → Connectors → Add custom connector</b> in Claude.",
    "Paste the URL above.",
    "Sign in with Google or email when the browser window opens. <b>You only do this once per device.</b>",
  ],
  chatgpt: [
    "Turn on <b>Developer Mode</b> in ChatGPT (Settings → Apps &amp; Connectors → Advanced settings).",
    "Click <b>Create</b> under Apps &amp; Connectors and paste the URL above as the MCP Server URL.",
    "Sign in with Google or email when the browser window opens. <b>You only do this once per device.</b>",
  ],
};

function renderSteps(card, host) {
  const stepsEl = card.querySelector(".steps");
  stepsEl.innerHTML = STEPS[host]
    .map(
      (body, i) =>
        `<div class="step"><span class="num">${i + 1}</span><span class="body">${body}</span></div>`
    )
    .join("");
}

function setupConnectCard(card) {
  const pills = card.querySelectorAll(".pill");
  pills.forEach((pill) => {
    pill.addEventListener("click", () => {
      pills.forEach((p) => p.classList.remove("active"));
      pill.classList.add("active");
      renderSteps(card, pill.dataset.host);
    });
  });

  const copyBtn = card.querySelector(".copy-btn");
  copyBtn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(CLOUD_URL);
      const original = copyBtn.textContent;
      copyBtn.textContent = "Copied";
      setTimeout(() => {
        copyBtn.textContent = original;
      }, 1500);
    } catch (err) {
      // Clipboard API unavailable (e.g. insecure context) — URL is still
      // visible and selectable in the text field, so this is a soft failure.
    }
  });

  renderSteps(card, "claude");
}

document.querySelectorAll(".connect-card").forEach(setupConnectCard);

document.querySelectorAll("[data-open-connect]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const card = document.querySelector(btn.dataset.openConnect);
    card.classList.add("open");
    card.scrollIntoView({ behavior: "smooth", block: "nearest" });
  });
});

document.querySelectorAll("[data-close-connect]").forEach((btn) => {
  btn.addEventListener("click", () => {
    btn.closest(".connect-card").classList.remove("open");
  });
});
