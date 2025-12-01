ACT AS A SENIOR FRONTEND DEVELOPER & UX ARCHITECT.

Goal: Design and implement the HOME / DASHBOARD page for the “Spectrum Open Source” web application.  
This is the FIRST PAGE the user sees right after confirming login (post-auth redirect).

CONTEXT

- Backend: Python + Flask.
- Frontend: HTML5, CSS3, Vanilla JS (no framework), talking to Flask via REST (JSON).
- Auth: User is already logged in (session or JWT handled by backend). When accessing `/home` or `/dashboard`, the page must:
  - Fetch `/api/user/me` to get user info.
  - Fetch `/api/dashboard/summary` to get stats (projects, simulations, storage, etc.).

The product is a professional RF engineering / coverage-analysis tool similar to Spectrum-E, with modules for:
- Project / Network management (FM/TV).
- Technical analysis & interference (Fail/Fail + Deygout).
- Coverage maps & population analysis.
- Knowledge base (IBGE + Plano Básico da Anatel).
- RF calculators and utilities.

You must create a **modern, clean, responsive dashboard** that works as the main HUB of the system.

DELIVERABLES

Generate 3 files, ready to drop into a Flask project:

1) `templates/home.html`
2) `static/css/home.css`
3) `static/js/home.js`

Do NOT use any build tools (no bundlers, no frameworks). Plain HTML/CSS/JS only.

--------------------------------
1. LAYOUT & UX REQUIREMENTS
--------------------------------

General:

- Layout type: Top header + main content area with a **card grid**.
- Visual style: Corporate, clear, slightly inspired by engineering tools.
  - Primary color: deep blue (e.g., #0d2b4e).
  - Secondary: neutros (cinza claro, branco).
  - Use CSS variables in `:root` for colors (e.g., `--primary`, `--bg`, `--card-bg`, `--accent`).
- Must be fully responsive:
  - Desktop: 3 columns of cards.
  - Tablet: 2 columns.
  - Mobile: 1 column, stacked.

Sections:

A) Top Header (Fixed)

- Full-width bar at the top.
- Left side:
  - Logo text: **“Spectrum Open Source”** (can be plain text styled as logo).
  - Small subtitle: “RF Coverage & Interference Analyzer”.
- Center or left side (navigation):
  - Text links (no SPA router, just `<a>`):
    - “Home” → `/home`
    - “Redes / Projetos” → `/projects`
    - “Mapas” → `/maps`
    - “Base de Dados” → `/database`
- Right side (user info):
  - Circular avatar icon (simple placeholder).
  - User name from `/api/user/me` (e.g., “Bem-vindo, Fernando”).
  - A dropdown or simple menu with:
    - “Perfil”
    - “Sair” (logout – call `/auth/logout` or configurable endpoint).

B) Welcome / Status Strip

Immediately below the header, a horizontal section that shows:

- Text: “Bem-vindo, {full_name}”.
- Smaller text: “Login: {email} | Acesso expira em: {X dias}”.
- Summary badges (small pills):
  - “Projetos: {total_projects}”
  - “Simulações: {total_simulations}”
  - “Coberturas salvas: {total_artifacts}”
- These values must be loaded dynamically from `/api/dashboard/summary` (JSON).

C) Main Card Grid (Central Hub)

A grid of “apps” (cards). Each card is visually clickable, with:

- Icon (can be `<span>` or inline SVG).
- Title in bold.
- 2–3 lines of description.
- A footer CTA like “Abrir” or “Acessar módulo”.

Required cards:

1. **Nova Análise Técnica**
   - Icon: something like a waveform / antenna.
   - Description:
     - “Criar um novo estudo técnico de viabilidade entre uma estação proposta e o plano básico (FM/TV).”
   - On click:
     - Go to `/analysis/new` (simple `window.location.href` in JS or `<a>`).

2. **Minhas Redes & Projetos**
   - Description:
     - “Gerenciar redes, cenários e projetos de estudo (FM, TV, 5G).”
   - On click:
     - `/projects`.

3. **Mapas & Coberturas**
   - Description:
     - “Visualizar manchas de cobertura, overlays no mapa, resultados de simulações e interferência.”
   - On click:
     - `/maps`.

4. **Base Regulatório & IBGE**
   - Description:
     - “Acessar plano básico da Anatel, setores censitários do IBGE e camadas vetoriais.”
   - On click:
     - `/database`.

5. **Ferramentas RF & Calculadoras**
   - Description:
     - “Conversores de unidades, FSPL, look angles, canais adjacentes, etc.”
   - On click:
     - `/tools`.

6. **Relatórios & Exportações**
   - Description:
     - “Gerar e baixar relatórios de cobertura, interferência e população coberta.”
   - On click:
     - `/reports`.

Optional Extra cards (if space allows):

7. **Últimas Simulações**
   - Description:
     - “Acessar rapidamente as últimas simulações executadas.”
   - On click:
     - `/simulations`.

8. **Admin / Configurações**
   - Only show if API returns `is_admin = true` in `/api/user/me`.
   - On click:
     - `/admin`.

--------------------------------
2. HTML (templates/home.html)
--------------------------------

Requirements:

- Use `extends` only if you assume a base template; otherwise, generate a full HTML document (I prefer a full self-contained file for now).
- Body structure:
  - `<header>` for the top bar.
  - `<main>` containing:
    - A `<section>` for welcome/status strip.
    - A `<section>` for the card grid (`.card-grid`).
- Include the CSS and JS via:
  - `<link rel="stylesheet" href="{{ url_for('static', filename='css/home.css') }}">`
  - `<script src="{{ url_for('static', filename='js/home.js') }}" defer></script>`

--------------------------------
3. CSS (static/css/home.css)
--------------------------------

Styling goals:

- Use `box-sizing: border-box;`.
- `:root` with variables:
  - `--primary`, `--primary-dark`, `--bg`, `--card-bg`, `--text`, `--muted`, `--accent`.
- Header:
  - Height ~ 56–64px.
  - Flexbox: align logo, nav, user section.
- Welcome strip:
  - Light background, subtle border-bottom.
  - Use flex layout, wrap in mobile.
- Card grid:
  - CSS Grid:
    - `grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));`
  - Cards:
    - Padding ~ 16–20px.
    - Rounded corners (8–12px).
    - Shadow on hover.
    - Slight scale on hover (transform: scale(1.02)).
- Make sure typography looks clean:
  - `body` font: system font stack or `Roboto`, 14–16px base.

--------------------------------
4. JAVASCRIPT (static/js/home.js)
--------------------------------

Behavior:

- On DOMContentLoaded:
  1) Fetch user info from `/api/user/me`.
     - Expected JSON:
       - `{ "full_name": "...", "email": "...", "days_left": 7, "is_admin": true/false }`
     - Update:
       - Welcome text.
       - Email display.
       - Days remaining.
       - Show admin card if `is_admin` is true.
  2) Fetch dashboard summary from `/api/dashboard/summary`.
     - Expected JSON:
       - `{ "total_projects": 12, "total_simulations": 34, "total_artifacts": 18 }`
     - Update the summary badges.
- Add click handlers on cards (if not using simple `<a>`):
  - `document.querySelectorAll('.dashboard-card').forEach(card => { card.addEventListener('click', () => { ... }) })`
- Handle fetch errors gracefully:
  - If API fails, show a small warning banner (“Falha ao carregar dados do painel”).
- Implement logout button:
  - On click → `fetch('/auth/logout', { method: 'POST' })` then `window.location.href = '/login';`
  - Keep URL configurable at the top of the file as constants if needed.

--------------------------------
5. CODE QUALITY & STRUCTURE
--------------------------------

- Use semantic HTML tags.
- No inline styles.
- No inline JS.
- Keep JS modular:
  - Separate functions: `loadUser()`, `loadSummary()`, `initCards()`.
- Write clear comments explaining what each block does.

FINAL OUTPUT

Produce the 3 files as if they were real project files:

1) `templates/home.html`
2) `static/css/home.css`
3) `static/js/home.js`

All code must be consistent and directly usable inside a Flask project.
No pseudocode. Use real, working HTML/CSS/JS.
