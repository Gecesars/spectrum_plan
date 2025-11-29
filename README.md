# Spectrum Open Source (spectrum_plan)

Backend on-premise para planejamento/viabilidade de RF conforme especificações Anatel (FM/TV), com PostGIS, pipeline ETL IBGE, motor de terreno SRTM, propagação (FSPL/Deygout) e API Flask/Celery.

## Visão geral
- **Stack**: Python 3.10+, Flask, SQLAlchemy/GeoAlchemy2, Postgres + PostGIS, Celery + Redis, NumPy/SciPy, rasterio/pycraf, Matplotlib.
- **Dados locais**: `./SRTM` (tiles .hgt), `./Knowledge_base` (shapefiles/CSV IBGE, XML Anatel). Não são versionados.
- **Módulos principais**:
  - Modelos GIS e usuários/projetos/estações (`app/models.py`).
  - ETL IBGE (`scripts/ingest_kb.py`).
  - Terreno SRTM (`app/core/terrain.py`).
  - Propagação/heatmap (`app/core/propagation.py`).
  - API + Celery (`app/main.py`, `app/tasks.py`).
  - Fase 2 regulatória (busca de vizinhos, PR, contornos, Deygout) em `app/regulatory/`.

## Requisitos
- PostgreSQL 15+ com PostGIS (dev/WSL recomendado).
- Redis para filas Celery.
- Python 3.10+ (venv já disponível).

## Configuração
1) Crie/envie um `.env` na raiz com, por exemplo:
   ```
   DATABASE_URL=postgresql+psycopg2://user:pass@localhost:5432/spectrum
   SECRET_KEY=dev-secret
   REDIS_URL=redis://localhost:6379/0
   CELERY_BROKER_URL=redis://localhost:6379/0
   CELERY_RESULT_BACKEND=redis://localhost:6379/0
   ```
2) Ative o venv existente ou crie um novo e instale dependências:
   ```
   source venv/bin/activate
   pip install -r requirements.txt
   ```
3) Garanta PostGIS habilitado:
   ```sql
   CREATE EXTENSION IF NOT EXISTS postgis;
   ```
4) Mantenha os diretórios de dados:
   - `SRTM/` com tiles `.hgt` (defina `SRTMDATA` se usar pycraf).
   - `Knowledge_base/` com shapefile IBGE (ex.: `BR_setores_CD2022.shp`) e CSV de demografia.

## Ingestão de dados
- Shapefile IBGE:
  ```
  python -m scripts.ingest_kb shp Knowledge_base/BR_setores_CD2022.shp
  ```
- CSV de demografia (coluna `CD_SETOR` obrigatória):
  ```
  python -m scripts.ingest_kb csv Knowledge_base/demografia.csv
  ```

## Execução
1) Suba o worker Celery:
   ```
   celery -A app.tasks.celery_app worker --loglevel=info
   ```
2) Inicie a API Flask:
   ```
   python -m app.main
   ```

### Endpoints principais
- `POST /api/project/<id>/station`: cria estação.
- `POST /api/simulation/start`: dispara cálculo de cobertura (Celery).
- `GET /api/simulation/<id>/status`: status/resultados.
- `GET /api/analytics/population?simulation_id=...`: soma população/households nos polígonos intersectados.

## Testes
- Requer PostGIS acessível (usa `TEST_DATABASE_URL` ou `DATABASE_URL`):
  ```
  pytest
  ```
  - Em CI usamos PostGIS via container (`postgis/postgis:14-3.3`); workflow em `.github/workflows/ci.yml`.
  - Cobertura inclui modelos, ETL, terreno, propagação e caso FM 98.1 vs 98.3 MHz @ 15 km no módulo regulatório.

## Estrutura
- `app/models.py` – ORM (User, Project, Station, VectorLayer/Feature, Simulation).
- `app/core/terrain.py` – leitura/cache de tiles .hgt.
- `app/core/propagation.py` – heatmap de cobertura FSPL + inclinação.
- `app/regulatory/` – search (ST_DWithin), protection ratios, contornos rápidos, Deygout/difração com heatmap de interferência.
- `app/tasks.py` – Celery task para cobertura.
- `scripts/ingest_kb.py` – ingestão shapefile/CSV IBGE.
- `tests/` – pytest com fixtures PostGIS.

## Próximos passos
- Ajustar configs para o Postgres/Redis da VPS e incluir pipeline de migrações (Alembic).
- Conectar frontend (Google Maps) ao PNG/GeoJSON gerados pelo worker.
- Preencher `Knowledge_base` e `SRTM` no servidor; adicionar monitoração/log centralizado para ETL e workers.
