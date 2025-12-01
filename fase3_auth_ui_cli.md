# Spectrum Open Source — Fase 3  
## Autenticação, Páginas Flask e Integração Front–Back

Este arquivo descreve a **Fase 3** do projeto, focada em:

- Autenticação completa (login, logout, registro, confirmação de e-mail).
- Hash de senha seguro com Argon2 (argon2id).
- Integração Flask-Login.
- Templates HTML (Jinja2) de login, registro, home, projetos.
- CLI para gerenciamento de usuários.
- Integração entre frontend (páginas) e backend (API/DB) em cima da Fase 1.

Abaixo está o *prompt* completo para ser usado em um modelo de IA voltado a código (Gemini, GPT, Claude, etc.).

---

```text
ACT AS A SENIOR FLASK BACKEND ENGINEER & FULLSTACK ARCHITECT.

Project: Spectrum Open Source
Current Status:
- Phase 1: Backend core, DB models, ETL, SRTM terrain, basic API skeleton (already designed).
- Phase 2: RF viability logic (math/physics) already specified but not necessarily implemented yet.

Phase 3 Goal:
Implement a COMPLETE authentication + UI integration layer for the web app:
- Secure login + logout + registration + e-mail confirmation (token).
- Password hashing with Argon2 (argon2id).
- Flask-Login integration (session-based auth).
- HTML templates (Jinja2) for login, register, home, projects, etc.
- Flask CLI commands for user management (createUser, listUsers, setPassword, promoteAdmin, etc.).
- Clean integration between frontend pages and existing backend APIs.

You MUST assume the Phase 1 structure exists (app/__init__.py, app/models.py, etc.) and EXTEND it.

# ======================================================
# 0. SECURITY & PASSWORD HASHING (ARGON2)
# ======================================================

Use the library: `argon2-cffi`

0.1. Update requirements.txt
- Add: `argon2-cffi`
- If needed: `Flask-Login`

0.2. Update User model in app/models.py

- Ensure the password hash column supports long Argon2 encoded strings:
  - `password_hash = db.Column(db.String(255), nullable=False)` (255 is enough, but you may use 512 if you prefer margin).
- Implement methods inside User:

  - `set_password(self, raw_password: str) -> None`
    - Uses Argon2 (argon2id) with sane defaults:
      - time_cost (e.g. 2 or 3)
      - memory_cost (e.g. 102400 or 256000 kB)
      - parallelism (e.g. 8)
      - type argon2.Type.ID
    - Stores the encoded hash in `self.password_hash`.

  - `check_password(self, raw_password: str) -> bool`
    - Verifies using Argon2.
    - Must catch verification exceptions and return False.

- Consider also a boolean flag:
  - `is_admin = db.Column(db.Boolean, default=False)` to control admin privileges.

0.3. Password policy:
- Enforce minimal policy:
  - At least 8 chars.
  - At least 1 digit.
  - At least 1 letter.
- Implement small helper (e.g. `validate_password_strength(password: str) -> tuple[bool, str | None]`).

# ======================================================
# 1. FLASK-LOGIN INTEGRATION
# ======================================================

Use `Flask-Login` for session management.

1.1. Setup in app/extensions.py
- Create `login_manager = LoginManager()`.
- In `init_extensions(app)`, call `login_manager.init_app(app)`.
- Set:
  - `login_manager.login_view = "auth.login"`  (blueprint.endpoint)
  - `login_manager.session_protection = "strong"`

1.2. User loader
- In app/models.py or app/extensions.py, define:

  ```python
  @login_manager.user_loader
  def load_user(user_id: str) -> User | None:
      return User.query.get(int(user_id))
  ```

- Make `User` inherit from `UserMixin` (from flask_login) or implement:
  - `is_authenticated`
  - `is_active`
  - `get_id`

1.3. Remember-me (opcional)
- In login route, support "remember me" checkbox and pass `remember=True|False` to `login_user`.

# ======================================================
# 2. AUTH ROUTES & PAGES (FRONT + BACK)
# ======================================================

Implement a full auth flow:

2.1. Routes in app/api/routes_auth.py (or app/auth/routes.py if you prefer a separate package)

Blueprint: `auth_bp = Blueprint("auth", __name__, url_prefix="/auth")`

REQUIRED ROUTES (HTML):

- GET `/auth/login`
  - Renders `templates/auth/login.html`.
  - If user is already authenticated, redirect to `main.home`.

- POST `/auth/login`
  - Receives `email`, `password`, `remember` from form.
  - Validates inputs.
  - Finds `User` by email.
  - Uses `user.check_password(password)` to verify.
  - If OK → `login_user(user, remember=remember)`.
  - Redirect to home (`url_for("main.home")`).
  - If FAIL → re-render login with error message.

- GET `/auth/register`
  - Renders `templates/auth/register.html`.
  - Only accessible if not logged in (optional: allow only admin to create users).

- POST `/auth/register`
  - Inputs: `full_name`, `email`, `password`, `password_confirm`.
  - Verifies strong password, confirms equality.
  - Creates new user with `is_verified=False`.
  - Generates `verification_token` (secure random string).
  - Saves to DB.
  - (Phase 3: you may just show the token on screen or log it, instead of sending real email.)
  - Redirect to `auth.login` with info: "Check your email" or "Use this token to confirm: ...".

- GET `/auth/confirm/<token>`
  - Finds user by token.
  - If found → set `is_verified=True`, `verification_token=None`.
  - Redirect to login with success message.

- GET `/auth/logout`
  - Calls `logout_user()`.
  - Redirect to login page.

OPTIONAL (nice to have, but you may stub):
- `/auth/forgot_password` (GET/POST)
- `/auth/reset_password/<token>` (GET/POST)

2.2. Templates (HTML + Jinja2)

Create templates:

- `templates/base.html`
  - Contains `<head>` with CSS links, `<body>` with a `{% block content %}`.
  - Top navigation bar that shows:
    - Logo (Spectrum Open Source)
    - If user authenticated: "Home", "Projects", "Simulations", "Logout".
    - If not authenticated: "Login" / "Register".
  - Include block `{% block scripts %}` at end for per-page JS.

- `templates/auth/login.html`
  - Extends `base.html`.
  - Contains a centered login card:
    - Email field
    - Password field
    - Remember me checkbox
    - Submit button
    - Link: "Create account" -> `/auth/register`
  - Shows flash messages for errors / success.

- `templates/auth/register.html`
  - Extends `base.html`.
  - Simple registration form.

- `templates/home.html`
  - Extends `base.html`.
  - This is the same dashboard described in the "Phase 3 – Home Page" spec:
    - Welcome message using `current_user.full_name`.
    - Cards: Technical Analysis, My Networks, My Simulations, My Files, RF Calculators, Documentation.
  - Each card should link to their endpoints (`/projects`, `/simulations`, etc).

2.3. Main blueprint (pages) – app/api/routes_main.py

Create blueprint:
- `main_bp = Blueprint("main", __name__)`

Routes:

- GET `/`
  - If not authenticated → redirect to `auth.login`.
  - If authenticated → render `home.html`.

- GET `/projects`
  - `@login_required`
  - Renders `projects/index.html` which lists user projects.
  - This page should call an API or query DB directly (for Phase 3 you can query DB in the view).

- GET `/projects/<int:project_id>`
  - Shows basic project details and possible actions.

(You don't need to implement full CRUD UI for all entities in Phase 3, but at least the skeleton pages must exist.)

# ======================================================
# 3. FRONT–BACK INTEGRATION (JS + JSON)
# ======================================================

In addition to pure HTML forms, integrate with the JSON APIs defined in Phase 1 where relevant:

3.1. Dashboard JS

- Create `static/js/dashboard.js`.
- On `home.html`, include this script (inside `{% block scripts %}`).
- On page load:
  - Option 1: Use Jinja to embed `current_user` directly (simpler).
  - Option 2: Call `/api/auth/me` (JSON) and render dynamic info via JS.
- You MUST implement `/api/auth/me` in `routes_auth.py` returning JSON:

  ```json
  { "id": 1, "full_name": "...", "email": "...", "is_admin": true }
  ```

3.2. Projects page integration

- `GET /projects` (HTML) should internally call the DB or hit `/api/projects` to list projects.
- You can demonstrate both:
  - Server-side render list of projects.
  - Or load via JS (Fetch) from `/api/projects` and render on client.

# ======================================================
# 4. FLASK CLI – USER MANAGEMENT
# ======================================================

We need a clean CLI to manage users and some system tasks.

Use Flask's built-in CLI integration (click).

4.1. In `app/__init__.py` (inside create_app), register CLI commands.

Implement a module `app/cli.py` (or integrate in `app/__init__.py`).

We want a `flask user` command group with:

- `flask user.create`
  - Options:
    - `--email`
    - `--full-name`
    - `--password` (if omitted, prompt securely)
    - `--admin/--no-admin` (default: no-admin)
  - Behavior:
    - Validates if email is unique.
    - Creates the user, hashes password with Argon2.
    - Prints "User created with id=...".

- `flask user.list`
  - Shows table with: id, email, full_name, is_admin, is_verified, created_at.

- `flask user.set-password`
  - Options:
    - `--email`
    - `--password` (prompt if not provided)
  - Updates password_hash via Argon2.

- `flask user.promote`
  - Options:
    - `--email`
  - Sets `is_admin=True`.

Additionally, create a top-level shortcut:
- `flaskUser.createUser` equivalent:
  - You can implement as alias to `flask user.create`, or define command name `create-user`.
  - Document in comments how to run:
    - `flask user.create --email test@example.com --full-name "Test" --password secret --admin`

4.2. Optional CLI for ETL (Phase 1 integration)

- `flask kb.ingest-anatel`
- `flask kb.ingest-ibge`
- That internally calls the functions from `scripts/ingest_knowledge_base.py`.

# ======================================================
# 5. TESTS (AUTH & CLI)
# ======================================================

Update /tests to cover new pieces:

5.1. `tests/test_auth.py`
- Scenario 1:
  - Create test user in DB (using fixture).
  - Use `client.post("/auth/login", data={"email": "...", "password": "..."})`.
  - Expect redirect to `/`.
  - Use `client.get("/")` and check that it returns 200.

- Scenario 2:
  - Wrong password → login page shows error, does NOT log in.

5.2. `tests/test_cli_users.py`
- Use Flask CLI runner fixture: `runner = app.test_cli_runner()`.
- Run commands:
  - `runner.invoke(args=["user.create", "--email", "cli@test.com", "--full-name", "CLI User", "--password", "Secret123"])`
  - Assert exit code 0.
  - Check that `User.query.filter_by(email="cli@test.com").first()` exists.

# ======================================================
# 6. CLEANUP & QUALITY
# ======================================================

- All new templates must be valid HTML5.
- Use Bootstrap (via CDN) or simple custom CSS for layout; keep it clean and modern.
- Add docstrings to:
  - Auth routes
  - CLI commands
  - ElevationProvider (Phase 1 code, if not already done).

- Make sure ALL imports are correct and consistent across files.
- App must be runnable with:

    export FLASK_APP="app.main:create_app"
    flask run

  or a similar pattern (document exactly how).

END OF SPECIFICATION FOR PHASE 3 (AUTH + UI + CLI)
```

---

Use este arquivo como especificação oficial da **Fase 3** ao instruir a IA de código a gerar os arquivos Flask, templates, JS e CLI.
