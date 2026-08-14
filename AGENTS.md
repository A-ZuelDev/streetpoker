# StreetPoker Agent Instructions

## Project

StreetPoker is a browser-based, play-money poker platform.

Current phase: Phase 0 / project foundation.

Do not implement poker gameplay unless explicitly assigned.

## Priorities

1. Correctness
2. Hidden-information security
3. State consistency
4. Reconnection reliability
5. Automated testing
6. Maintainability
7. User experience
8. Visual polish

## Architecture

The server is authoritative.

The frontend must never decide:

- cards
- winners
- turn order
- legal actions
- pot calculations
- stack changes

Never send concealed cards to unauthorized clients.

Keep the poker domain separate from:

- FastAPI
- WebSockets
- SQLAlchemy
- PostgreSQL
- React
- frontend code

## Initial stack

Backend:

- Python
- FastAPI
- Pydantic
- SQLAlchemy
- Alembic
- PostgreSQL
- pytest
- Hypothesis
- Ruff
- mypy

Frontend:

- React
- TypeScript
- Vite
- Zustand
- TanStack Query
- Zod
- Vitest
- React Testing Library
- Playwright

Infrastructure:

- Docker Compose
- GitHub Actions

## Do not add yet

Do not add unless explicitly requested:

- Redis
- Kubernetes
- Kafka
- RabbitMQ
- microservices
- GraphQL
- AWS deployment
- authentication
- accounts
- payments
- poker gameplay
- variants
- tournaments

## Agent workflow

Before important edits:

1. Read this file.
2. Inspect existing code.
3. Explain the proposed approach.
4. State which files will change.
5. State which dependencies are required.
6. State how the work will be tested.

Ask before installing or upgrading dependencies.

After implementation:

1. Run tests.
2. Run linting.
3. Run formatting checks.
4. Run type checking.
5. Summarize changed files.
6. Report limitations or concerns.
7. Commit only when explicitly instructed.

## Git

Never make important changes directly on main.

Use focused branches.

Do not force-push.

Do not merge unless explicitly instructed.

Do not modify unrelated files.

## Testing

Never weaken a test simply to make implementation pass.

Important future poker invariants include:

- Chips are conserved.
- Players cannot act out of turn.
- Folded players cannot act.
- Stacks cannot become negative.
- Players cannot wager more than their stack.
- Pots reconcile exactly.
- Only eligible players can win pots.
- Cards cannot be dealt twice.
- Hidden cards cannot leak.
- Duplicate commands cannot execute twice.