import { api, $, goTo, pageHref, setSessionToken } from "/js/common.js";

const form = $("#code-form");
const views = $("#views");
const errorBox = $("#code-error");
const viewError = document.createElement("p");
viewError.className = "error-text hidden";
viewError.setAttribute("role", "alert");
views.appendChild(viewError);
const next = new URLSearchParams(location.search).get("next");
const safeNext = next && next.startsWith("/") && !next.startsWith("//") ? next : null;

function showViews() {
  form.classList.add("hidden");
  views.classList.remove("hidden");
}

async function init() {
  try {
    await api("/api/me", { redirect: false });
    if (safeNext) { location.href = pageHref(safeNext); return; }
    showViews();
  } catch (e) { /* not signed in yet */ }
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  errorBox.classList.add("hidden");
  try {
    const r = await api("/api/session", { method: "POST", body: { code: $("#code").value, role: "doctor" } });
    if (r && r.token) setSessionToken(r.token);
    if (safeNext) { location.href = pageHref(safeNext); return; }
    showViews();
  } catch (err) {
    errorBox.textContent = err.message || "Could not sign in.";
    errorBox.classList.remove("hidden");
  }
});

for (const tile of document.querySelectorAll(".view-tile")) {
  tile.addEventListener("click", async (e) => {
    e.preventDefault();
    viewError.classList.add("hidden");
    try {
      const r = await api("/api/session/role", { method: "POST", body: { role: tile.dataset.role }, redirect: false });
      if (r && r.token) setSessionToken(r.token);
    } catch (err) {
      // Never fail silently: say what happened and how to recover.
      viewError.textContent = err.status === 401
        ? "This browser did not keep the sign-in. Enter the access code again; if this keeps happening, open the link in Chrome, Edge or Safari."
        : `Could not open this view: ${err.message || "network error"}.`;
      viewError.classList.remove("hidden");
      if (err.status === 401) { views.classList.add("hidden"); form.classList.remove("hidden"); }
      return;
    }
    goTo(tile.getAttribute("href"), (msg) => { viewError.textContent = msg; viewError.classList.remove("hidden"); });
  });
}

init();
