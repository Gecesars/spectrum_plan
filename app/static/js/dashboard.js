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

async function fetchProjects() {
  const listEl = document.getElementById("projects-list");
  const countEl = document.getElementById("projects-count");
  if (!listEl) return;
  try {
    const res = await fetch("/api/projects");
    if (!res.ok) {
      listEl.innerHTML = "<p class='muted'>Não foi possível carregar projetos.</p>";
      return;
    }
    const projects = await res.json();
    if (Array.isArray(projects) && projects.length > 0) {
      listEl.innerHTML = projects
        .map(
          (p) =>
            `<article class="list-item"><div><h3>${p.name}</h3><p class="muted">${p.description || "Sem descrição"}</p></div><div class="list-actions"><a class="ghost" href="/map?project=${p.id}">Mapa</a></div></article>`
        )
        .join("");
    } else {
      listEl.innerHTML = "<p class='muted'>Nenhum projeto encontrado.</p>";
    }
    if (countEl) countEl.textContent = projects.length ?? 0;
  } catch (err) {
    console.error("Failed to load projects", err);
  }
}

function attachProjectForm() {
  const form = document.getElementById("new-project-form");
  if (!form) return;
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = document.getElementById("project-name").value.trim();
    const description = document.getElementById("project-description").value.trim();
    const feedback = document.getElementById("project-feedback");
    if (!name) return;
    form.querySelector("button")?.setAttribute("disabled", "disabled");
    try {
      const res = await fetch("/api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, description })
      });
      if (!res.ok) {
        const payload = await res.json().catch(() => ({}));
        if (feedback) {
          feedback.style.display = "block";
          feedback.classList.add("error");
          feedback.textContent = payload.error || "Erro ao criar projeto.";
        }
      } else {
        form.reset();
        if (feedback) {
          feedback.style.display = "block";
          feedback.classList.remove("error");
          feedback.classList.add("success");
          feedback.textContent = "Projeto criado com sucesso.";
        }
        await fetchProjects();
      }
    } catch (err) {
      console.error("Failed to create project", err);
    } finally {
      form.querySelector("button")?.removeAttribute("disabled");
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  fetchUser();
  fetchSummary();
  fetchProjects();
  attachProjectForm();
});
