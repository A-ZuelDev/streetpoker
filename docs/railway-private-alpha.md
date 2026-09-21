# Railway private-alpha plan

Phase 13B freezes the Railway setup for StreetPoker. It does not create a Railway project,
connect GitHub, change DNS, or deploy the application. Follow this plan during Phase 13C only
after the Phase 13B checkpoint is committed and merged.

Railway documentation checked on 2026-09-21:

- [Dockerfiles](https://docs.railway.com/builds/dockerfiles)
- [Monorepos](https://docs.railway.com/deployments/monorepo)
- [Healthchecks](https://docs.railway.com/deployments/healthchecks)
- [Pre-deploy commands](https://docs.railway.com/deployments/pre-deploy-command)
- [PostgreSQL](https://docs.railway.com/databases/postgresql)
- [Domains](https://docs.railway.com/networking/domains/working-with-domains)
- [Scaling](https://docs.railway.com/deployments/scaling)
- [Cost control](https://docs.railway.com/pricing/cost-control)

## Non-negotiable operating limit

The backend must have exactly one Railway replica in one region and its existing Docker command
must retain `--workers 1`. Do not add a second region or replica, enable horizontal autoscaling,
or override the start command.

Rooms, memberships, hands, timers, reconnect state, and Stand-Up state are process-local. A
backend restart, crash, redeploy, rollback, sleep, or scale operation deletes every active room
and disconnects its clients. PostgreSQL does not persist any of that state. Railway does not
provide sticky sessions, and stickiness would not make multiple backend processes safe anyway.

Disable Serverless/app sleeping for the backend. Treat every backend deployment as a scheduled
full-room reset and notify alpha users first.

## Frozen topology

Create one Railway project and one private-alpha environment with these services:

| Service | Source | Public | Required shape |
| --- | --- | --- | --- |
| `frontend` | GitHub repository, root `/frontend` | HTTPS domain | one persistent service |
| `backend` | GitHub repository, root `/backend` | HTTPS domain | one region, one replica, one Uvicorn worker |
| `Postgres` | Railway PostgreSQL | no public TCP proxy | private `DATABASE_URL`/PG variables only |

Use Railway HTTP public networking for the two web services. Do not expose PostgreSQL publicly
and do not attach a volume to either application service.

The frontend and backend are isolated projects inside one repository. With each service root set
to its subdirectory, Railway finds the existing `Dockerfile` at that root. Leave Railway build
and start command overrides empty so the checked-in Dockerfiles remain authoritative. Set the
source branch to `main` in Phase 13C.

No `railway.toml` or `railway.json` is included. Railway can use the existing Dockerfiles and
service settings directly, while its legacy Config as Code feature is deprecated. Capturing the
whole project as Railway Infrastructure as Code can wait until the topology is stable.

## Create and configure before connecting GitHub

This order prevents an incomplete service from deploying:

1. Create an empty Railway project and its private-alpha environment.
2. Add Railway PostgreSQL and name it exactly `Postgres`. Keep public networking disabled.
3. Add empty persistent services named exactly `backend` and `frontend`.
4. Configure each service using the tables below.
5. Generate one Railway-provided HTTPS domain for each web service.
6. Add the cross-service reference variables after both domains exist.
7. Review every staged setting, then connect both services to the same GitHub repository and
   `main` branch. Connecting the repository is the first action that should trigger application
   builds.

### Backend settings

| Setting | Value |
| --- | --- |
| Root Directory | `/backend` |
| Builder | detected `backend/Dockerfile` |
| Build Command | blank |
| Start Command | blank; use the Docker `CMD` |
| Port variable | `PORT=8000` |
| Healthcheck Path | `/ready` |
| Healthcheck Timeout | `300` seconds |
| Pre-deploy Command | `python -m alembic -c alembic.ini upgrade head` |
| Pre-deploy Timeout | `300` seconds |
| Region/replicas | one selected region, exactly one replica |
| Serverless/app sleeping | disabled |
| Deployment overlap | `0` seconds |
| Deployment draining | `0` seconds |
| Restart policy | On Failure; remember that any restart loses all rooms |

`/ready` is the deployment gate because it verifies PostgreSQL connectivity. `/health` remains
the process-only liveness endpoint for manual diagnosis. Railway healthchecks run while a new
deployment starts; they are not continuous production monitoring.

The migration runs once in Railway's pre-deploy container before the new backend becomes active.
A non-zero Alembic exit blocks that backend deployment. Do not put migrations into the backend
start command and do not run a separate long-lived migration replica.

Set these backend service variables:

```dotenv
PORT=8000
STREETPOKER_ENVIRONMENT=production
STREETPOKER_DATABASE_URL=postgresql+psycopg://${{Postgres.PGUSER}}:${{Postgres.PGPASSWORD}}@${{Postgres.PGHOST}}:${{Postgres.PGPORT}}/${{Postgres.PGDATABASE}}
STREETPOKER_CORS_ORIGINS=["https://${{frontend.RAILWAY_PUBLIC_DOMAIN}}"]
RAILWAY_HEALTHCHECK_TIMEOUT_SEC=300
RAILWAY_DEPLOYMENT_OVERLAP_SECONDS=0
RAILWAY_DEPLOYMENT_DRAINING_SECONDS=0
```

The explicit `postgresql+psycopg://` prefix selects the installed Psycopg 3 driver; Railway's
plain `postgresql://` URL would make this SQLAlchemy version select the uninstalled Psycopg 2
driver. The remaining components are references to Railway-managed Postgres variables and use
private networking. Do not use `DATABASE_PUBLIC_URL`.

`STREETPOKER_CORS_ORIGINS` is a JSON list. Each value must be an exact browser origin: HTTPS
scheme plus hostname, with no path or trailing slash. Add an origin only when users actually load
the frontend from it.

### Frontend settings

| Setting | Value |
| --- | --- |
| Root Directory | `/frontend` |
| Builder | detected `frontend/Dockerfile` |
| Build Command | blank |
| Start Command | blank; use the Docker `CMD` |
| Port variable | `PORT=8080` |
| Healthcheck Path | `/healthz` |
| Healthcheck Timeout | `300` seconds |
| Replicas | one is sufficient for the private alpha |
| Serverless/app sleeping | disabled for predictable availability |

Set these frontend service variables:

```dotenv
PORT=8080
VITE_API_BASE_URL=https://${{backend.RAILWAY_PUBLIC_DOMAIN}}
RAILWAY_HEALTHCHECK_TIMEOUT_SEC=300
```

`VITE_API_BASE_URL` is intentionally public and is compiled into the browser bundle. The
frontend derives `https://<backend>/rooms`, `https://<backend>/health`, and
`wss://<backend>/ws/rooms/<ROOM_CODE>` from it. Do not include a trailing slash, path, query,
credentials, or secret.

Railway service variables are available to Docker builds, and the frontend Dockerfile declares
`ARG VITE_API_BASE_URL`. A missing value therefore fails the image build instead of silently
shipping the localhost default.

## Domains, TLS, and CORS

Use Railway-provided `*.up.railway.app` domains for the first deployment. Railway terminates TLS
for public HTTP services and supports WebSockets, so the browser uses HTTPS and WSS without an
application-side certificate.

Custom domains are optional after the Railway-domain smoke test. Add and verify the Railway DNS
records, wait for its certificate, then change both references together:

- backend `STREETPOKER_CORS_ORIGINS` to the exact custom frontend origin;
- frontend `VITE_API_BASE_URL` to the custom backend HTTPS URL.

Changing `VITE_API_BASE_URL` requires a frontend rebuild. Changing CORS redeploys the backend,
which destroys active rooms. Keep the Railway-provided domains until the custom-domain deployment
has passed HTTP, CORS, and WebSocket smoke checks.

## Secrets

Do not copy `.env.alpha` to Railway and never commit Railway credentials or generated values.
Use service variables and references. The frontend variables and every `VITE_` value are public.
Database credentials belong only to `Postgres` and the backend reference variable. Seal any
manually entered secret that does not need to be viewed again.

Do not paste variable values, database URLs, or full environment dumps into issues, chat, build
logs, or screenshots. Log keys and service names only when diagnosing configuration.

## Cost controls

Before connecting GitHub in Phase 13C:

1. Set a compute-usage email alert at a deliberately low private-alpha threshold.
2. Set a compute hard limit that is acceptable for the billing cycle. Reaching it takes workloads
   offline and therefore destroys active rooms.
3. Keep both application services at one replica and PostgreSQL private to avoid unnecessary
   compute and egress.
4. Set conservative per-replica CPU and memory limits only after a successful smoke test shows a
   safe baseline; a limit that is too low can crash the backend and lose rooms.
5. Review Railway usage and metrics after the first test session and after every configuration
   change.

Do not use backend Serverless sleep as a cost control: waking creates a new process with no rooms.

## Phase 13C deployment and smoke gate

Phase 13C may perform the external setup only with explicit authorization. Its completion gate is:

1. Merge the reviewed Phase 13B checkpoint into `main` and push the intended commit.
2. Create/configure the project in the order above; inspect staged settings before deploying.
3. Confirm the Postgres service is healthy and has no public TCP proxy.
4. Confirm the backend pre-deploy Alembic command succeeds.
5. Confirm exactly one backend replica and the runtime command contains `--workers 1`.
6. Confirm frontend `GET /healthz`, backend `GET /health`, and backend `GET /ready` return
   success through their public HTTPS domains.
7. Load the frontend and verify browser requests use the public backend HTTPS domain and the room
   socket uses the matching WSS domain.
8. Create a room, join from a second browser/device, play a short hand, exercise Stand-Up, and
   verify reconnect behavior.
9. Perform an intentional maintenance restart only after warning testers; verify the old room is
   gone and a fresh room works. This confirms the documented ephemeral-state boundary.
10. Record domains, region, service settings, deployment commit, smoke results, and current usage
    without recording secrets.

Passing build and deployment healthchecks does not prove poker flow, WebSocket continuity, CORS,
or room persistence. Only the Phase 13C multi-client smoke test validates the deployed alpha.
