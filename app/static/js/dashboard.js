async function fetchUser() {
  try {
    const res = await fetch("/api/user/me");
    const user = await res.json();
    document.getElementById("user-name").textContent = user.name || "User";
    document.getElementById("user-email").textContent = user.email || "";
    document.getElementById("welcome-name").textContent = user.name || "User";
    document.getElementById("welcome-email").textContent = user.email || "";
    document.getElementById("welcome-days").textContent = user.days_left ?? "--";
  } catch (err) {
    console.error("Failed to load user", err);
  }
}

document.getElementById("logout-btn")?.addEventListener("click", () => {
  // Placeholder logout behavior; replace with real auth logout.
  alert("Logout placeholder - integrate auth flow.");
});

fetchUser();
