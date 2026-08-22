# Phase 7 Hold'em stress runner

This test-only command repeatedly exercises the public domain path from `TableState` through
`HoldemHand`, legal actions, terminal pot construction, and `settle_holdem_hand`. It is not a bot,
gameplay service, or production API, and pytest does not collect it during normal test runs.

From the repository root, display its options:

```powershell
.\backend\.venv\Scripts\python.exe -m backend.tests.stress.holdem_stress --help
```

Run a deterministic smoke sweep:

```powershell
.\backend\.venv\Scripts\python.exe -m backend.tests.stress.holdem_stress --hands 1000 --seed 20260822
```

Larger manual sweeps use the same command with a larger `--hands` value, such as `100000`.
Performance depends on the machine and is a benchmark rather than a correctness requirement.

Every case is independently derived from the master seed and case index. To replay one exact case
without running earlier indexes:

```powershell
.\backend\.venv\Scripts\python.exe -m backend.tests.stress.holdem_stress --seed 20260822 --case-index 17
```

From the backend project directory, use the test package directly:

```powershell
.\.venv\Scripts\python.exe -m tests.stress.holdem_stress --help
.\.venv\Scripts\python.exe -m tests.stress.holdem_stress --hands 1000 --seed 20260822
.\.venv\Scripts\python.exe -m tests.stress.holdem_stress --seed 20260822 --case-index 17
```

On the first failure the runner exits nonzero and prints the case index, master/case/action/deck
seeds, complete scenario, all successfully resolved actions, exception, traceback, and exact replay
command.

## Current API limitations

- `HoldemHandSnapshot` is trusted server state and intentionally contains every hole card; there is
  no viewer projection whose concealment can be tested yet.
- Burn-card identities are private, so only visible-card uniqueness and total deck consumption can
  be checked externally.
- Commands have no IDs or hand versions, so full stale-command and retry idempotency semantics are
  not available yet.
- `HoldemHand` does not expose its internal `Deck` or `BettingRound`, so internal aliasing cannot be
  inspected directly.
- Production snapshots do not embed replay metadata; this runner retains scenario seeds and action
  traces externally.
