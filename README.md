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
- **[Pydantic](https://docs.pydantic.dev/)** — data validation and settings
- **[Uvicorn](https://www.uvicorn.org/)** — ASGI server
- **python-dotenv** — environment variable management
- **Docker** — containerized deployment (`DOCKERFILE`, `docker-compose.yml`)

## Project structure

```
app/
├── agent/      # Agent-related logic
├── config/     # App configuration and environment settings
├── data/       # Data access layer
├── model/      # Domain / database models
├── routers/    # API route definitions
├── schema/     # Pydantic request/response schemas
├── services/   # Business logic
└── .env.example
```

This project is in early scaffolding — modules are stubbed out and will be
filled in as functionality is built.

## Getting started

Install dependencies:

```bash
pip install -r requirements.txt
```

Copy the example environment file and fill in the required values:

```bash
cp app/.env.example app/.env
```

## Requirements files

- `requirements.txt` — development dependencies
- `requirements-prod.txt` — production dependencies
