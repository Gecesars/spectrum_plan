ACT AS A SENIOR PYTHON BACKEND ENGINEER & SOFTWARE ARCHITECT.

Project Name: Spectrum Open Source – Phase 1 (Backend Core)
Goal: Implement a fully working backend foundation before any complex RF physics: database schema, ingestion of IBGE/Anatel data, SRTM terrain access, Flask skeleton, Celery skeleton, and tests.

# ==============================
# 0. GENERAL REQUIREMENTS
# ==============================

Tech stack (MANDATORY):
- Python 3.10+
- Flask (as web framework)
- SQLAlchemy + Flask_SQLAlchemy (ORM)
- PostgreSQL 15+ with PostGIS extension (spatial database)
- GeoAlchemy2 (for Geometry columns)
- Psycopg2 or psycopg2-binary (PostgreSQL driver)
- GeoPandas (for shapefile ingestion)
- Pandas (CSV/XLSX ingestion)
- Rasterio (for SRTM elevation tiles)
- NumPy (math / arrays)
- Celery + Redis (for async tasks – only skeleton in Phase 1)
- pytest (test framework)

Filesystem conventions:
- SRTM elevation data will be in local folder: `./SRTM`
- Knowledge base (IBGE, Anatel, CSV, XML, shapefiles) will be in: `./Knowledge_base`
- App code root: `./spectrum_open_source` (or similar - you decide and keep consistent).

Your job: generate **real, production-grade code**, with comments explaining *why* for each critical part.

DELIVERABLES (directory structure):

Create a structure like:

/spectrum_open_source
│
├── app
│   ├── __init__.py
│   ├── config.py
│   ├── extensions.py        # db, migrate, celery, etc.
│   ├── models.py            # Core relational + GIS models
│   ├── gis_models.py        # (optional) if you prefer to separate GIS classes
│   ├── core
│   │   ├── terrain.py       # SRTM ElevationProvider
│   │   └── db_utils.py      # small DB helpers if needed
│   ├── api
│   │   ├── __init__.py
│   │   ├── routes_auth.py   # login/check user
│   │   ├── routes_projects.py
│   │   └── routes_debug.py  # healthcheck / ping
│   ├── celery_app.py        # Celery instance/config (skeleton)
│   └── main.py              # Flask app factory / entrypoint
│
├── scripts
│   ├── ingest_knowledge_base.py   # ETL for IBGE + Anatel
│   └── init_db.py                 # create_all, migrations bootstrap
│
├── SRTM/                   # (already present, just assume tiles here)
├── Knowledge_base/         # (already present, with XML/CSV/SHP)
│
├── tests
│   ├── conftest.py
│   ├── test_models.py
│   ├── test_etl.py
│   └── test_terrain.py
│
├── requirements.txt
└── pyproject.toml or setup.cfg (optional, if you want)

You MUST output the full content of all these files (or most important ones) in several code blocks, but keep everything consistent.

# ==============================
# 1. ENVIRONMENT & CONFIG
# ==============================

1.1. Create a `requirements.txt` with all necessary libs:
- flask
- flask_sqlalchemy
- flask_migrate
- psycopg2-binary
- geoalchemy2
- numpy
- pandas
- geopandas
- rasterio
- celery
- redis
- pytest
- python-dotenv (optional)
- any minimal dependencies you really need.

1.2. `app/config.py`

Implement a Config class (and DevConfig) with:

- DB URI (placeholder): `SQLALCHEMY_DATABASE_URI = "postgresql+psycopg2://user:password@localhost:5432/spectrum_db"`
- `SQLALCHEMY_TRACK_MODIFICATIONS = False`
- `SECRET_KEY` placeholder
- `CELERY_BROKER_URL` = "redis://localhost:6379/0"
- `CELERY_RESULT_BACKEND` = "redis://localhost:6379/1"

Allow overriding via environment variables.

1.3. `app/extensions.py`

- Create and export:
  - `db = SQLAlchemy()`
  - `migrate = Migrate()`
  - `celery = Celery()` (just initialize, configure later)
- Provide `init_extensions(app)` function to init db, migrate etc.

1.4. `app/__init__.py`

- Implement Flask app factory `create_app(config_class=Config)`:
  - Load config
  - Initialize extensions
  - Register blueprints from `app/api`
  - Provide a simple healthcheck route like `/api/health` returning JSON.

# ==============================
# 2. DATABASE SCHEMA (MODELS)
# ==============================

Use SQLAlchemy models with GeoAlchemy2 for geometry. Use PostGIS SRID 4326.

Create in `app/models.py`:

2.1. `User`

- Fields:
  - `id` (Integer, PK)
  - `email` (String, unique, not null)
  - `password_hash` (String)
  - `full_name` (String)
  - `is_active` (Boolean, default True)
  - `is_verified` (Boolean, default False)
  - `verification_token` (String, nullable)
  - `created_at` (DateTime, default=utcnow)
- Relationships:
  - `projects` → Project (one-to-many)

2.2. `Project`

Represents a "network/study" like “Rio de Janeiro – FM 2025”.

- Fields:
  - `id` (PK)
  - `user_id` (FK to users.id, not null)
  - `name` (String, not null)
  - `description` (Text, nullable)
  - `created_at`, `updated_at`
- Relationships:
  - `stations` → Station
  - `simulations` → Simulation (optional, but useful)

2.3. `AntennaModel`

Stores reusable antenna patterns.

- Fields:
  - `id` (PK)
  - `name` (String)
  - `manufacturer` (String)
  - `gain_dbi` (Float)
  - `horizontal_pattern` (JSONB, required)  # e.g., { "0": 0, "10": -1.2, ... }
  - `vertical_pattern` (JSONB, nullable)

2.4. `Station`

Core RF object.

- Fields:
  - `id` (PK)
  - `project_id` (FK projects.id)
  - `name` (String)
  - `station_type` (String; "FM", "TV", "AM", "5G", etc.)
  - `status` (String; "Proposed", "Existing")
  - `latitude` (Float, not null)
  - `longitude` (Float, not null)
  - `site_elevation` (Float, default 0.0)
  - `frequency_mhz` (Float, not null)
  - `channel_number` (Integer, nullable)
  - `erp_kw` (Float, not null)
  - `antenna_height` (Float, not null)
  - `service_class` (String, nullable)
  - `antenna_model_id` (FK antenna_models.id, nullable)
  - `azimuth` (Float, default 0.0)
  - `mechanical_tilt` (Float, default 0.0)
  - `polarization` (String, default "Circular")

2.5. `Simulation`

Stores metadata for each coverage/interference calculation (Phase 2 will use it heavily).

- Fields:
  - `id` (PK)
  - `project_id` (FK projects.id)
  - `station_id` (FK stations.id)
  - `calc_type` (String; e.g., "coverage_itu1546", "interference_deygout")
  - `resolution_m` (Integer; 1, 30, 100, etc.)
  - `status` (String; "PENDING", "RUNNING", "COMPLETED", "FAILED")
  - `result_file_path` (String; path to PNG/GeoTIFF)
  - `created_at` (DateTime)

2.6. GIS layer catalog – `VectorLayer` and `VectorFeature`

Create `VectorLayer`:

- Fields:
  - `id` (PK)
  - `name` (String; e.g. "IBGE Setores 2022 – RJ")
  - `user_id` (FK users.id, nullable; NULL → system layer)
  - `geom_type` (String: "POLYGON", "MULTIPOLYGON", "LINESTRING", "POINT")
  - `is_visible` (Boolean, default True)

Create `VectorFeature`:

- Fields:
  - `id` (PK)
  - `layer_id` (FK vector_layers.id)
  - `properties` (JSONB; must store key like "cd_setor" or "cod_ibge")
  - `geom` (Geometry, MULTIPOLYGON, SRID=4326)
- You MUST create a spatial index on `geom` using GeoAlchemy2 / SQLAlchemy syntax.

2.7. `ProjectArtifact`

Stores overlay images / artifacts generated by simulations:

- Fields:
  - `id` (PK)
  - `simulation_id` (FK simulations.id)
  - `artifact_type` (String; "overlay_png", "raw_geotiff", "kml_export")
  - `file_path` (String; e.g. `/static/projects/1/cov_001.png`)
  - `bounds` (JSONB; keys: north, south, east, west)
  - `style_metadata` (JSONB; optional color scales etc.)

Give all models `__repr__` methods and docstrings.

# ==============================
# 3. ETL / INGESTION PIPELINE
# ==============================

Create `scripts/ingest_knowledge_base.py`:

Goal: Ingest IBGE shapefiles and Anatel plan data into PostGIS once, then the app uses DB (no external APIs).

Assume the following files exist inside `./Knowledge_base`:
- `plano_basicoTVFM.xml`  (Anatel: existing FM/TV stations)
- `setores_2022_RJ.shp`   (IBGE census sectors shapefile – example)
- `setores_2022_RJ.csv`   (IBGE aggregated attributes with population, etc.)

3.1. DB connection

- Use SQLAlchemy `create_engine(DATABASE_URI)` reading from environment or config.
- Use GeoPandas `.to_postgis()` to insert geometry data.

3.2. Function: `ingest_anatel_xml(xml_path: str)`

- Parse XML using `xml.etree.ElementTree`.
- For each station entry:
  - Extract: frequency, latitude, longitude, class, ERP, name (Entidade + Município).
  - Convert coordinate to decimal degrees (you MUST implement a helper; if not sure of exact format, assume decimal with comma and show how to adapt).
  - Map into `stations` table with:
    - `status="Existing"`
    - `station_type="FM"` or `"TV"` (you can infer by service type tag if exists; else default to FM with TODO comment).
    - `project_id` → 1 (a special system project called "BASE NACIONAL").
- Use Pandas DataFrame + `to_sql` (if simple) or insert row by row; but must be robust.

3.3. Function: `ingest_ibge_shapefile(shp_path: str, layer_name: str)`

- Use GeoPandas to read shapefile.
- Ensure CRS = EPSG:4326: `gdf = gdf.to_crs("EPSG:4326")` if needed.
- Insert a `VectorLayer` row for this dataset.
  - Example: `name = "IBGE Setores 2022 – RJ"`, `geom_type = "MULTIPOLYGON"`.
- Save each feature into `vector_features` with:
  - `layer_id` set.
  - `properties` JSONB containing key `cd_setor` (or equivalent).
  - `geom` loaded via GeoAlchemy/GeoPandas `.to_postgis()`.
- Handle geometry errors:
  - Skip invalid geometries and log them to `etl_errors.log`.

3.4. Function: `merge_ibge_attributes(csv_path: str, layer_id: int)`

- Load CSV with Pandas.
- For each row:
  - Extract `CD_SETOR` or equivalent.
  - Build a JSON with relevant attributes: population, households, income, etc.
- Use SQL `UPDATE`:
  - `UPDATE vector_features SET properties = properties || :json WHERE properties->>'cd_setor' = :cd_setor`
- Use SQLAlchemy `text()` and execute in batches.
- Log rows that don’t match any `cd_setor`.

3.5. CLI entrypoint

At bottom of `ingest_knowledge_base.py`:

- Implement `if __name__ == "__main__":`:
  - Parse arguments or just call:
    - `ingest_anatel_xml(...)`
    - `ingest_ibge_shapefile(...)`
    - `merge_ibge_attributes(...)`

# ==============================
# 4. TERRAIN ENGINE (SRTM)
# ==============================

Create `app/core/terrain.py`:

Goal: Provide a **fast** way to query elevations along any path using local SRTM tiles inside `./SRTM`.

Assumptions:
- SRTM tiles named like: `S23W044.hgt` or `.tif`.
- 1 arc-second or 3 arc-second; you may assume one resolution and note where to adjust.

4.1. Class: `ElevationProvider`

Constructor:

- Accept parameter `base_path: str = "./SRTM"`.

Private helper: `_get_tile_filename(lat: float, lon: float) -> str`

- Implement naming convention:
  - N/S: `N` if lat >= 0 else `S`
  - E/W: `E` if lon >= 0 else `W`
  - Integer degrees:
    - `lat_int = abs(int(floor(lat)))`
    - `lon_int = abs(int(floor(lon)))`
  - Format: `"{NS}{lat:02d}{EW}{lon:03d}.hgt"`
- Return just file name (no path).

Cached method: `get_raster(tile_name: str)`

- Use `functools.lru_cache(maxsize=10)`.
- Open file via `rasterio.open(os.path.join(base_path, tile_name))`.
- If not found, raise a clear exception.

Method: `get_elevation(lat: float, lon: float) -> float`

- Computes tile file name.
- Opens (or gets cached) raster.
- Calls `sample([(lon, lat)])` and returns height, or 0 if error.

Method: `get_elevation_profile(lat_list: list[float], lon_list: list[float]) -> list[float]`

- For each pair `(lat, lon)`:
  - Determine tile.
  - Reuse cached raster.
  - Append value to result.
- Do this efficiently (avoid reopening file).

Add docstrings and logging.

# ==============================
# 5. FLASK API – BASIC BACKEND
# ==============================

Create simple blueprints in `app/api`:

5.1. `routes_auth.py`

- Blueprint: `auth_bp`, prefix `/api/auth`.
- Route: `GET /api/auth/me`
  - Returns JSON: `{ "full_name": "...", "email": "...", "is_admin": false }`
  - For Phase 1, you may stub authentication (e.g., assume user 1).
- Route: `POST /api/auth/login` (stub; no real auth needed yet, just example).

5.2. `routes_projects.py`

- Blueprint: `projects_bp`, prefix `/api/projects`.
- Route: `GET /api/projects`
  - Returns list of projects of current user.
- Route: `POST /api/projects`
  - Creates a new project with minimal fields.

5.3. `routes_debug.py`

- Blueprint: `debug_bp`, prefix `/api/debug`.
- Route: `GET /api/debug/health`
  - Returns `{ "status": "ok", "db": "ok" or "error", "timestamp": ... }`

Your code must show how to register these blueprints in `create_app`.

# ==============================
# 6. CELERY SKELETON (NO COMPLEX TASKS YET)
# ==============================

Create `app/celery_app.py`:

- Implement `make_celery(app)` function that creates a Celery instance bound to Flask.
- Configure broker/result_backend from `app.config`.
- Expose `celery` singleton in `extensions.py` or here.
- Create a dummy task `debug_add(x, y)` just to prove it works.

In Phase 1 we only need this skeleton; no real RF tasks yet.

# ==============================
# 7. TEST SUITE (PYTEST)
# ==============================

Create `tests/conftest.py`:

- Pytest fixture for `app` using `create_app(TestConfig)`.
- Fixture for a test database (e.g., Postgres test schema or SQLite with geometry stub if easier – but prefer real PostGIS if possible).
- Fixture `client` (Flask test client).
- Optionally fixture to setup & teardown tables.

7.1. `tests/test_models.py`

- Test 1: Create a `User`, commit, query back, assert email.
- Test 2: Create a `Project` linked to user.
- Test 3: Create a `VectorLayer` and one `VectorFeature` with a simple polygon geometry using WKT string (e.g. "POLYGON ((...))").
- Use `ST_AsText` check if geometry is stored.

7.2. `tests/test_etl.py`

- Mock a small GeoDataFrame in memory with 2 polygons and property `cd_setor`.
- Call a simplified version of `ingest_ibge_shapefile` (or a helper) inserting into test DB.
- Assert that 2 rows exist in `vector_features`.
- If possible, also test an update using `merge_ibge_attributes` with a fake CSV DataFrame.

7.3. `tests/test_terrain.py`

- Create a tiny mock raster file within a temp directory, e.g. a 10x10 grid with known heights using rasterio.
- Instantiate `ElevationProvider` pointing to that temp directory.
- Call `get_elevation(...)` and verify the value matches the raster cell.
- Test `get_elevation_profile` with multiple points.

All tests must be runnable with:

    pytest -q

# ==============================
# 8. CODE QUALITY
# ==============================

- Use type hints in Python (`def func(x: float) -> float:`).
- Add docstrings to important classes and functions.
- Log meaningful messages on ETL steps and errors (`logging` module).
- Avoid pseudocode; provide fully working implementations.


END OF SPECIFICATION FOR PHASE 1
