# Spectrum Open Source (spectrum_plan)

Portal web e API Flask para planejamento/viabilidade de RF (FM/TV), com autenticação, dashboard, consultas GIS (PostGIS) e ETL de bases IBGE/Anatel.

## O que está pronto
- App factory `create_app`, blueprints (`auth`, `web`, `api/projects`, `api/core`), CLI de usuários.
- Argon2 + Flask-Login (login/registro/confirmar e-mail, logout) e telas estilizadas em `app/templates/auth`.
- Dashboard e páginas base (`home`, `projects`, `map`, `database`, etc.) em `base_portal.html` com CSS/JS em `app/static`.
- API e tasks: criação de estação, enfileiramento de simulação, analytics, tiles GeoJSON, Celery worker (`app/tasks.py`).
- ETL IBGE: ingestão de shapefile/CD_SETOR e merge de demografia + agregação por município (`scripts/ingest_kb.py`).

## Requisitos
- Python 3.10+ (use o `venv` já presente).
- PostgreSQL 15+ com PostGIS (dev/prod). SQLite atende apenas autenticação simples; recursos GIS/tarefas usam PostGIS.
- Redis (para Celery) opcional em dev.

## Configuração rápida
1) Copie o exemplo de env e ajuste URLs:
   ```bash
   cp .env.example .env
   # edite DATABASE_URL (ex.: postgresql+psycopg2://atx:123@127.0.0.1:5432/atxcover)
   ```
2) Ative o ambiente e instale dependências:
   ```bash
   source venv/bin/activate
   pip install -r requirements.txt
   ```
3) Garanta PostGIS:
   ```sql
   CREATE EXTENSION IF NOT EXISTS postgis;
   ```
4) Crie tabelas (usa metadata diretamente):
   ```bash
   flask shell -c "from app.config import init_db; init_db()"
   ```
   (Se quiser Alembic: `flask db init` -> `flask db migrate` -> `flask db upgrade`.)

## Rodando
```bash
export FLASK_APP=app:create_app
flask run               # API + páginas
celery -A app.tasks.celery_app worker -l info   # worker (opcional para simulações)
```

## ETL e dados (Knowledge_base)
- Ingestão dos setores censitários (tile friendly):
  ```bash
  python -m scripts.ingest_kb shp Knowledge_base/Ibge/BR_setores_CD2022.shp --layer ibge_setores_cd2022
  ```
- Converter Excel de população para CSV e mesclar por CD_SETOR:
  ```bash
  python - <<'PY'
  import pandas as pd
  pd.read_excel("Knowledge_base/CD2022_Populacao_Coletada_Imputada_e_Total_Municipio_e_UF_20231222.xlsx").to_csv(
      "Knowledge_base/Ibge/populacao.csv", index=False
  )
  PY
  python -m scripts.ingest_kb csv Knowledge_base/Ibge/populacao.csv
  ```
- Criar layer agregado por município (para tiles):
  ```bash
  python -m scripts.ingest_kb municipios --source ibge_setores_cd2022 --target ibge_municipios_2022
  ```
- Tiles GeoJSON: `/api/tiles/ibge_setores_cd2022/<z>/<x>/<y>?limit=500&cd_mun=3304557`

## Principais rotas
- Autenticação: `/auth/login`, `/auth/register`, `/auth/confirm/<token>`, `/auth/logout`, `/api/auth/me`.
- Projetos: `GET/POST /api/projects` (usado pela página `/projects`).
- Core: `/api/health`, `POST /api/project/<id>/station`, `POST /api/simulation/start`, `GET /api/simulation/<id>/status`, `GET /api/analytics/summary`, `GET /api/tiles/<layer>/<z>/<x>/<y>`.
- Estáticos/HTML: `home`, `projects`, `map`, `files`, `calculators`, `docs` em `app/templates`.

## Frontend rápido
- Layout base: `app/templates/base_portal.html`.
- Dashboard (cartões) e projetos (form + listagem dinâmica) usam `app/static/css/dashboard.css` e `app/static/js/dashboard.js`.
- Login/cadastro estilizados e responsivos; ver flashes para senhas fracas/duplicadas.

## CLI útil
```bash
flask user.create --email admin@spectrum.test --full-name "Admin" --password Strong123 --admin
flask user.list
flask user.promote --email admin@spectrum.test
```

## Testes
Requer PostGIS acessível via `TEST_DATABASE_URL` ou `DATABASE_URL`:
```bash
pytest
```

## Notas de deploy
- Produção em VPS Linux: mantenha Postgres com PostGIS, Redis, e diretórios `SRTM/` e `Knowledge_base/` montados fora do repositório.
- Atualize `.env` com credenciais SMTP para envio real de e-mail de confirmação.
