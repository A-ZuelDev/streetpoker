# Private-alpha deployment runbook

Phase 13A packages StreetPoker as provider-neutral containers for a small private alpha. It does
not deploy anything or add durable room storage.

## Required operating model

Run exactly one backend container with exactly one Uvicorn worker. Rooms, memberships, active
hands, reconnect timers, and Stand-Up state live only in that process's memory.

- Restarting, replacing, or redeploying the backend deletes every active room and disconnects all
  clients.
- Multiple backend replicas or `--workers` greater than `1` split room state and are unsupported.
- Load-balancer stickiness does not make multiple processes safe because room creation, reconnect,
  timers, and authority remain process-local.
- PostgreSQL currently supports readiness and future persistence work; it does not persist rooms.

Tell alpha users before maintenance and treat every backend restart as a full room reset. Durable
rooms and multi-process coordination are later phases.

## Topology

`compose.alpha.yaml` runs three provider-neutral services:

1. PostgreSQL 17 for the existing readiness contract.
2. One FastAPI/Uvicorn backend process on port 8000.
3. One Nginx container serving the compiled React application on port 8080.

Terminate TLS at a provider load balancer or host reverse proxy. The default host bindings are
loopback-only for that topology. Set either bind address to `0.0.0.0` only when the surrounding
network and firewall intentionally protect the exposed port.

The browser bundle receives the public backend URL at image build time. Use `https://` so its
WebSocket connections become `wss://`. The backend CORS list must contain the exact public
frontend origin.

## Configure without committing secrets

Create the ignored runtime file and restrict its filesystem permissions:

```powershell
Copy-Item .env.alpha.example .env.alpha
```

Replace every example value in `.env.alpha`. Use a long random PostgreSQL password and put its
URL-encoded form in `STREETPOKER_DATABASE_URL` if it contains reserved URL characters. Never put a
secret in `PUBLIC_API_BASE_URL` or any `VITE_` variable; those values are embedded in browser code.

For a split provider deployment, configure the same values in that provider's secret and build-
argument systems instead of copying `.env.alpha` to the host.

## Build and start

Validate the resolved configuration before starting anything:

```powershell
docker compose --env-file .env.alpha -f compose.alpha.yaml config --quiet
docker compose --env-file .env.alpha -f compose.alpha.yaml build
```

Apply migrations as a one-off task, then start the three services:

```powershell
docker compose --env-file .env.alpha -f compose.alpha.yaml run --rm backend `
  python -m alembic -c alembic.ini upgrade head
docker compose --env-file .env.alpha -f compose.alpha.yaml up -d
docker compose --env-file .env.alpha -f compose.alpha.yaml ps
```

Do not add `--scale backend`, override the backend command, or configure an external platform to
launch more than one replica.

## Verify and operate

Check the backend through its public TLS endpoint:

```powershell
Invoke-RestMethod https://api.example.com/health
Invoke-RestMethod https://api.example.com/ready
```

Then load the public frontend, create a room, join from a second browser profile or device, play a
short hand, and verify reconnect behavior. `/health` proves that the process is alive; `/ready`
also verifies PostgreSQL connectivity. Neither endpoint proves that a previously created room
survived, because rooms are intentionally ephemeral.

Before a planned restart, notify users that all rooms will be lost. Inspect service logs without
printing `.env.alpha`:

```powershell
docker compose --env-file .env.alpha -f compose.alpha.yaml logs --tail 200 backend frontend
```

Do not expose PostgreSQL publicly. Backing up its volume does not back up active rooms in this
phase.

## Phase 13A exit boundary

This checkpoint provides reproducible production packaging and an operator runbook only. It does
not add persistence, Redis, multi-process coordination, provider-specific infrastructure, DNS,
TLS certificates, monitoring, backups, or an external deployment.

Phase 13B selects Railway and freezes its service settings without deploying. See
[the Railway private-alpha plan](railway-private-alpha.md) for the three-service topology,
variables, migration and healthcheck order, one-replica constraint, cost controls, and the exact
Phase 13C deployment gate.
