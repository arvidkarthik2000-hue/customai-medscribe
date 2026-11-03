import { api, $ } from "/js/common.js";

const form = $("#code-form");
const views = $("#views");
const errorBox = $("#code-error");
const next = new URLSearchParams(location.search).get("next");

function showViews() {
  form.classList.add("hidden");
  views.classList.remove("hidden");
}

async function init() {
  try {
    await api("/api/me", { redirect: false });
    if (next && next.startsWith("/") && !next.startsWith("//")) { location.href = next; return; }
    showViews();
  } catch { /* not signed in yet */ }
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  errorBox.classList.add("hidden");
  try {
    await api("/api/session", { method: "POST", body: { code: $("#code").value, role: "doctor" } });
    if (next && next.startsWith("/") && !next.startsWith("//")) { location.href = next; return; }
    showViews();
  } catch (err) {
    errorBox.textContent = err.message || "Could not sign in.";
    errorBox.classList.remove("hidden");
  }
});

for (const tile of document.querySelectorAll(".view-tile")) {
  tile.addEventListener("click", async (e) => {
    e.preventDefault();
    try { await api("/api/session/role", { method: "POST", body: { role: tile.dataset.role } }); } catch { return; }
    location.href = tile.getAttribute("href");
  });
}

init();
