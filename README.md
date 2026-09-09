# Simlexx Wealth

Simlexx is a digital financial progress platform that connects people with
variable incomes to regulated investment and insurance providers. We help
people make financial progress, one contribution at a time.

Simlexx is not a fund manager and does not invest or hold customer money
itself. It builds the customer-facing experience and the infrastructure that
makes existing, regulated financial products easier to reach.

## Who it's for

The platform is built for people whose income doesn't fit traditional
financial-product structures:

- Informal-sector workers and market vendors
- Small business owners, gig workers, and boda boda riders
- Freelancers and contract employees
- Students and people dependent on allowances
- Anyone else with variable or irregular income

## What it does

Simlexx initially connects customers to investment products — such as unit
trusts and money market funds — through partnerships with regulated
providers, while embedding affordable microinsurance into that same journey.

## Tech stack

- **[FastAPI](https://fastapi.tiangolo.com/)** — web framework
- **[SQLAlchemy](https://www.sqlalchemy.org/)** (async) + **asyncpg** — ORM / database driver
- **[Alembic](https://alembic.sqlalchemy.org/)** — database migrations
- **[Pydantic](https://docs.pydantic.dev/)** — data validation and settings
- **[Uvicorn](https://www.uvicorn.org/)** — ASGI server
- **PostgreSQL** — primary database
- **Redis** — cache, plus Celery broker/result backend
- **[Celery](https://docs.celeryq.dev/)** — background jobs
- **Docker / Docker Compose** — local development and containerized deployment

## Project structure

```
app/
├── config/     # Settings (pydantic-settings) and env config
├── database/   # Async SQLAlchemy engine/session
├── middleware/ # CORS, etc.
├── model/      # SQLAlchemy models, enums, id generator
├── queue/      # Redis client and Celery app
├── routers/    # API routers (e.g. health_router.py)
├── schema/     # Pydantic request/response schemas
├── services/   # Business logic
├── main.py     # FastAPI app instance, wires everything together
├── routing.py  # Registers every router onto the app
└── startup.py  # Logging, migrations, lifespan, connectivity checks

alembic/        # Database migrations
docs/           # ER diagram, architecture diagram, database design doc
```

See [docs/Simlexx_Wealth_Database_Design.docx](docs/Simlexx_Wealth_Database_Design.docx)
for the full database schema and system architecture write-up, including the
diagrams below.

| ER diagram | Architecture |
|---|---|
| [docs/er_diagram.png](docs/er_diagram.png) | [docs/architecture_diagram.png](docs/architecture_diagram.png) |

## Running the app

The whole stack (API, worker, Postgres, Redis) runs via Docker Compose — this
is the recommended way to run it locally.

**1. Set up your environment file:**

```bash
cp .env.example .env
```

Fill in the values in `.env` (database credentials, Redis, any third-party
API keys you're using). See [.env.example](.env.example) for the full list.

**2. Build and start everything:**

```bash
docker compose up --build
```

This builds the `api` and `worker` images, starts `postgres` and `redis`,
waits for both to report healthy, then starts the API and the Celery worker.
On startup the API automatically runs `alembic upgrade head` against the
database — no manual migration step needed.

**3. Confirm it's up:**

```bash
curl http://localhost:8000/health
```

You should get back something like:

```json
{"status": "running", "components": {"database": "healthy", "cache": "healthy", "background": "healthy"}}
```

The API is now reachable at `http://localhost:8000`.

**Useful commands:**

```bash
docker compose logs -f api          # follow API logs
docker compose logs -f              # follow all services
docker compose down                 # stop and remove containers (keeps data)
docker compose up -d                # start in the background
```

**Running without Docker** (e.g. for quicker local iteration against
Docker-hosted Postgres/Redis): install dependencies, then run Uvicorn and
apply migrations yourself:

```bash
pip install -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload
```

## Requirements files

- `requirements.txt` — runtime dependencies
- `requirements-dev.txt` — runtime + dev tooling (pytest, mypy, etc.)
- `requirements-prod.txt` — production-only additions (currently empty)
