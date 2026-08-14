# StreetPoker

StreetPoker is a browser-based, play-money poker platform. Phase 0 establishes a typed,
tested foundation without implementing poker gameplay.

## Architecture

The server is authoritative. Future cards, winners, turn order, legal actions, pot calculations,
and stack changes must be decided by framework-independent backend domain code. Concealed cards
must never be sent to unauthorized clients.

The current application contains:

- a FastAPI backend with independent liveness and PostgreSQL readiness endpoints;
- a React, TypeScript, and Vite frontend that validates API data with Zod;
- PostgreSQL 17 for local development through Docker Compose;
- unit, integration, component, and browser smoke tests; and
- linting, formatting, strict type checking, and GitHub Actions CI.

TanStack Query owns remote API state. Zustand is installed for future client-only state and is
intentionally not used as a duplicate server-state cache.

## Windows prerequisites

- Windows with PowerShell
- Python 3.14.7 available through `py -3.14`
- Node.js 24 and npm
- Docker Desktop running Linux containers

All commands below start in the repository root:

```powershell
Set-Location C:\Projects\streetpoker
```

Direct virtual-environment executable paths are used so PowerShell script execution policy does
not need to allow `Activate.ps1`.

## Environment files

Create local configuration from the tracked examples:

```powershell
Copy-Item .env.example .env
Copy-Item backend\.env.example backend\.env
Copy-Item frontend\.env.example frontend\.env.local
```

The root file configures Docker Compose, the backend file contains server-only settings, and the
frontend file contains only the public `VITE_API_BASE_URL`. Never put secrets in a `VITE_`
variable because Vite exposes those variables to browser code.

## PostgreSQL

Validate Compose and start PostgreSQL 17:

```powershell
docker compose config
docker compose up -d postgres
docker compose ps
```

The `postgres` service should report `healthy`. View its logs if needed:

```powershell
docker compose logs postgres
```

Stop it without deleting local data:

```powershell
docker compose stop postgres
```

The named volume survives ordinary stops and `docker compose down`. The command
`docker compose down --volumes` permanently deletes local database data and should be used only
for an intentional reset.

## Backend

Create the Python 3.14 virtual environment and install the approved backend dependencies:

```powershell
py -3.14 -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install -e "backend[dev]"
```

Alembic is fully configured, but Phase 0 has no revision because no application schema exists yet.
With PostgreSQL running, validate the migration configuration:

```powershell
backend\.venv\Scripts\python.exe -m alembic -c backend\alembic.ini upgrade head
```

Start FastAPI:

```powershell
backend\.venv\Scripts\python.exe -m uvicorn streetpoker.api.app:create_app --factory --app-dir backend\src --reload --host 127.0.0.1 --port 8000
```

From another PowerShell window, verify both endpoints:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/ready
```

`GET /health` reports process liveness and never accesses PostgreSQL. `GET /ready` executes a
minimal query and returns HTTP 503 with a generic message when PostgreSQL is unavailable.

## Frontend

Install exactly the locked frontend dependency graph:

```powershell
npm --prefix frontend ci
```

Start Vite:

```powershell
npm --prefix frontend run dev -- --host 127.0.0.1
```

Open `http://127.0.0.1:5173`.

Install the Playwright Chromium browser once before running browser tests:

```powershell
npm --prefix frontend exec -- playwright install chromium
```

## Checks

Start PostgreSQL and apply Alembic before running the complete backend or browser suites.

Backend tests:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests
```

Frontend component tests:

```powershell
npm --prefix frontend run test -- --run
```

Python lint and formatting check:

```powershell
backend\.venv\Scripts\python.exe -m ruff check backend\src backend\tests backend\alembic
backend\.venv\Scripts\python.exe -m ruff format --check backend\src backend\tests backend\alembic
```

Python strict type checking:

```powershell
backend\.venv\Scripts\python.exe -m mypy backend\src
```

Frontend lint, formatting, and strict type checking:

```powershell
npm --prefix frontend run lint
npm --prefix frontend run format:check
npm --prefix frontend run typecheck
```

Production frontend build:

```powershell
npm --prefix frontend run build
```

With PostgreSQL and the backend server running, run the Chromium smoke test:

```powershell
npm --prefix frontend run test:e2e
```

Apply repository formatting when intentionally making formatting changes:

```powershell
backend\.venv\Scripts\python.exe -m ruff format backend\src backend\tests backend\alembic
npm --prefix frontend run format
```

## Phase 0 boundaries

Phase 0 contains no poker rules, cards, wagering, WebSockets, authentication, accounts, payments,
Redis, cloud deployment, queues, or microservices. The future domain boundary is documented in
`backend/src/streetpoker/domain/README.md`.
