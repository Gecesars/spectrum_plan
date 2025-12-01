# Spectrum Open Source — Fase 4  
## Integração Back–Front–Banco, ETL e Fluxos Finais

Este arquivo descreve a **Fase 4** do projeto, focada em:

- Sincronizar backend, frontend e banco de dados.
- Garantir que autenticação e páginas HTML estejam funcionando ponta a ponta.
- Popular o banco com vetores populacionais (IBGE) e Plano Básico (Anatel).
- Deixar a aplicação em ponto de execução com comandos claros (CLI, Flask).

Use este conteúdo como *prompt* em um modelo de IA voltado a código (Gemini / GPT / Claude etc.) para gerar/ajustar o código do projeto.

---

```text
ACT AS A SENIOR PYTHON/FLASK ARCHITECT & INTEGRATION ENGINEER.

Project: Spectrum Open Source

Previous Phases:
- Phase 1: Core backend, DB models, PostGIS, ETL skeletons, SRTM terrain engine, basic RF modules (planned).
- Phase 2: RF viability spec (protection ratios, Deygout, contour analysis) defined.
- Phase 3: Auth layer spec (Argon2, Flask-Login, HTML templates, CLI for users) defined and partially implemented.

Phase 4 Goal:
Synchronize ALL layers (backend, frontend, database, ETL, auth) and leave the application in a fully runnable state, with:
- Working DB connection and migrations.
- Populated vector/population datasets (IBGE + Plano Básico) in PostGIS.
- Login/Logout/Register fully working (with Argon2 hashing).
- Home dashboard + basic pages wired to real data.
- Minimal end-to-end tests (pytest) validating the full stack.

You MUST:
- Inspect and reconcile the existing structure (do not duplicate modules).
- Fix imports, blueprints, and app factory wiring.
- Make sure a developer can clone the repo, set env vars, run one or two commands, and have a working system.

======================================================
0. PROJECT STRUCTURE & CONFIG CHECK
======================================================

Assume a structure similar to:

- app/
  - __init__.py
  - config.py
  - extensions.py
  - models.py
  - core/
    - terrain.py
    - propagation.py
  - api/
    - routes_auth.py
    - routes_main.py
    - routes_projects.py
  - templates/
    - base.html
    - auth/login.html
    - auth/register.html
    - home.html
    - projects/index.html
  - static/
    - css/
    - js/
- scripts/
  - ingest_kb.py
- tests/
- .env.example
- requirements.txt

TASKS:

0.1. App Factory
- Open app/__init__.py and ENSURE there is a `create_app(config_class=None)` function that:
  - Creates Flask app.
  - Loads config from config_class or default config.
  - Initializes extensions (db, migrate, login_manager, etc.).
  - Registers blueprints: auth_bp, main_bp, projects_bp, and any API blueprints.
  - Registers CLI commands (user management, ETL).

0.2. Config & Environment
- In app/config.py:
  - Ensure there is a Config base class with:
    - SQLALCHEMY_DATABASE_URI (read from env, e.g. DATABASE_URL).
    - SECRET_KEY (from env).
    - SQLALCHEMY_TRACK_MODIFICATIONS = False.
  - Provide at least DevelopmentConfig and ProductionConfig.
- Create or fix .env.example with:
  - DATABASE_URL=
  - SECRET_KEY=
  - FLASK_ENV=development

======================================================
1. DATABASE & MIGRATIONS
======================================================

1.1. DB wiring
- Ensure app/extensions.py defines:
  - db = SQLAlchemy()
  - migrate = Migrate()
  - login_manager = LoginManager()
- Ensure create_app() calls:
  - db.init_app(app)
  - migrate.init_app(app, db)
  - login_manager.init_app(app)

1.2. Models sanity check
- Open app/models.py and verify:
  - User model:
    - id, email, password_hash, full_name, is_admin, is_verified, verification_token, created_at.
    - Proper length for password_hash (String(255) or 512).
  - Project, Station, Simulation, VectorLayer, VectorFeature, ProjectArtifact, etc.
  - GeoAlchemy2 Geometry fields have SRID=4326 and appropriate type (MULTIPOLYGON, etc.).

1.3. Alembic/Flask-Migrate
- Ensure migrations/ directory exists OR create it with:
  - `flask db init` (if needed).
- Generate migration scripts from models:
  - `flask db migrate -m "Initial schema"`
- Apply migrations:
  - `flask db upgrade`
- Add notes in comments on how to run these commands.

======================================================
2. AUTH INTEGRATION (BACK + FRONT)
======================================================

2.1. Argon2 hashing
- Verify argon2-cffi is in requirements.txt.
- In app/models.py (User class):
  - Implement set_password() and check_password() using Argon2 (argon2id).
  - Ensure imports and exceptions are correct.
  - Ensure password policy is enforced either in the route or in a helper.

2.2. Flask-Login plumbing
- Ensure User inherits from UserMixin or has methods required by Flask-Login.
- In extensions/login_manager:
  - Set login_view = "auth.login"
- Implement @login_manager.user_loader to return User by id.

2.3. Auth routes
- Open app/api/routes_auth.py (or app/auth/routes.py) and ensure:
  - Blueprint auth_bp is created and registered in create_app().
  - Routes:
    - GET/POST /auth/login
    - GET/POST /auth/register
    - GET /auth/logout
    - GET /auth/confirm/<token>
    - GET /api/auth/me (returns JSON with current_user info if logged in).

- Make sure:
  - login.html and register.html exist and extend base.html.
  - Flash messages are shown on errors.
  - Redirects after login go to "main.home" ("/").

2.4. Home & Navigation
- Open app/api/routes_main.py:
  - Ensure "/" is protected with @login_required and renders home.html.
- In templates/base.html:
  - Show navbar with different options based on `current_user.is_authenticated`.

======================================================
3. FRONTEND–BACKEND SYNC
======================================================

3.1. Home dashboard
- In templates/home.html:
  - Use Jinja to display "Welcome, {{ current_user.full_name }}".
  - Render a grid of cards (as specified in previous Home spec) linking to:
    - /analysis (or /technical)
    - /projects
    - /simulations
    - /files
    - /tools/calculators
    - /docs

3.2. Projects page
- In app/api/routes_projects.py:
  - Implement @login_required `GET /projects`:
    - Query Project.query.filter_by(user_id=current_user.id).
    - Render templates/projects/index.html with the list.
  - (Optional) Implement `POST /projects` to create a new project (simple form).
- In templates/projects/index.html:
  - Loop over projects and display name/description and link to /projects/<id>.

3.3. Static files
- Make sure static/js and static/css are referenced correctly in base.html using `url_for('static', filename='...')`.

======================================================
4. KNOWLEDGE BASE & POPULATION VECTORS (ETL)
======================================================

Goal: Populate PostGIS with georeferenced demographic data and Anatel’s base plan so the system can answer questions like "how many potential viewers in this cell?".

4.1. ETL script integration
- Open scripts/ingest_kb.py and:
  - Implement or refine:
    - ingest_anatel_xml()
    - ingest_ibge_vectors()
    - ingest_demographic_csv()
  - Use geopandas to load shapefiles (IBGE sectors).
  - Use pandas to load CSV/Excel for demographic aggregates.
  - Write to PostGIS using SQLAlchemy/GeoAlchemy (VectorLayer, VectorFeature).

4.2. CLI commands
- Expose ETL via Flask CLI:
  - `flask kb.ingest-anatel`
  - `flask kb.ingest-ibge`
  - `flask kb.ingest-demographics`
- Implement them in app/cli.py (or in __init__.py via @app.cli.command).
- Commands should:
  - Log progress.
  - Handle errors gracefully (skip bad rows, log to file).

4.3. Test data
- If full IBGE/Anatel dataset is too large for tests:
  - Create a small dummy shapefile/CSV in tests/data/ and write ETL tests against it.

======================================================
5. RF & SIMULATION STUB (MINIMAL WIRING)
======================================================

You do NOT need to fully implement the RF physics here, but you MUST make the pipeline coherent.

5.1. Simulation model usage
- Ensure Simulation table is hooked:
  - station_id (FK), calc_type, status, result_file_path, created_at.

5.2. Simulation endpoints
- Implement a simple endpoint:
  - POST /api/simulation/start
    - Input: station_id, maybe radius_km.
    - Creates Simulation row with status="PENDING".
    - (Option A) Immediately run a dummy worker that:
      - Writes a placeholder PNG or GeoTIFF in /static/overlays/.
      - Updates status="COMPLETED" and sets bounds metadata somewhere.
    - Return JSON { simulation_id, status }.

  - GET /api/simulation/<id>/status
    - Return JSON with status and, if completed, overlay URL and bounds.

This ensures the front-end flow can be tested even before full RF engine is coded.

======================================================
6. TESTS (END-TO-END SMOKE TESTS)
======================================================

Use pytest.

6.1. Auth tests
- tests/test_auth_flow.py:
  - Setup: create a user in the test DB (or via CLI call).
  - Test:
    - client.get("/auth/login") returns 200.
    - client.post("/auth/login", data={email, password}) redirects to "/".
    - After login, client.get("/") returns 200 and contains the user's name.

6.2. Projects tests
- tests/test_projects.py:
  - After login, create a project in the DB (Project with user_id).
  - client.get("/projects") returns 200 and lists the project name.

6.3. KB ETL tests
- tests/test_kb_etl.py:
  - Use small dummy shapefile or simple polygons.
  - Run ingest function against test database.
  - Assert VectorLayer and VectorFeature rows are created.

6.4. Simulation stub test
- tests/test_simulation_stub.py:
  - Create station.
  - Call POST /api/simulation/start (maybe via direct function call).
  - Assert Simulation row is created with proper fields.

======================================================
7. DEV EXPERIENCE & RUN INSTRUCTIONS
======================================================

7.1. requirements.txt
- Ensure it includes at least:
  - Flask
  - Flask-Login
  - Flask-Migrate
  - Flask-SQLAlchemy
  - psycopg2-binary (or async equivalent)
  - GeoAlchemy2
  - SQLAlchemy
  - argon2-cffi
  - numpy, scipy
  - rasterio, geopandas, shapely
  - pytest

7.2. README / comments
- Add a short "How to run" section (in comments or README-like docstring), e.g.:

  1. Create and activate virtualenv.
  2. Install requirements: `pip install -r requirements.txt`
  3. Set env vars (copy .env.example to .env).
  4. Initialize DB:
     - `flask db upgrade`
     - `flask user.create --email admin@example.com --full-name "Admin" --password Admin123 --admin`
  5. (Optional) Ingest Knowledge Base:
     - `flask kb.ingest-ibge`
     - `flask kb.ingest-anatel`
  6. Run:
     - `flask run`

7.3. Final validation
- Ensure that with a fresh DB, the minimal scenario works:
  - Create admin via CLI.
  - Login via /auth/login.
  - See home dashboard.
  - Access /projects (empty list).
  - No unhandled exceptions in logs.

END OF PHASE 4 SPECIFICATION – SYNCHRONIZE BACKEND, FRONTEND AND DATABASE, POPULATE KNOWLEDGE BASE, AND VALIDATE LOGIN + BASIC FLOWS.
```

---

Use este arquivo como especificação oficial da **Fase 4** ao instruir a IA de código a integrar todas as partes do sistema e deixá-lo em ponto de execução.
