async function fetchUser() {
  const nameEl = document.getElementById("welcome-name");
  const emailEl = document.getElementById("welcome-email");
  const daysEl = document.getElementById("welcome-days");
  const navName = document.getElementById("user-name");
  const navEmail = document.getElementById("user-email");
  try {
    const res = await fetch("/api/auth/me");
    if (!res.ok) return;
    const user = await res.json();
    const displayName = user.full_name || user.email || "User";
    if (nameEl) nameEl.textContent = displayName;
    if (emailEl) emailEl.textContent = user.email || "";
    if (daysEl) daysEl.textContent = user.days_left ?? "--";
    if (navName) navName.textContent = displayName;
    if (navEmail) navEmail.textContent = user.email || "";
  } catch (err) {
    console.error("Failed to load user", err);
  }
}

async function fetchSummary() {
  try {
    const res = await fetch("/api/analytics/summary");
    if (!res.ok) return;
    const summary = await res.json();
    document.getElementById("summary-projects")?.textContent = summary.projects ?? "--";
    document.getElementById("summary-simulations")?.textContent = summary.simulations ?? "--";
    document.getElementById("summary-artifacts")?.textContent = summary.artifacts ?? "--";
  } catch (err) {
    console.error("Failed to load summary", err);
  }
}

fetchUser();
fetchSummary();
