# Room application boundary

Phases 8 and 9A provide process-local coordination for private six-seat rooms and complete
single-hand application lifecycle orchestration. Code in this package is
independent of FastAPI, WebSockets, SQLAlchemy, PostgreSQL, and frontend transport models.

`RoomService` is the public command boundary. Every mutation receives a `GuestId`; nicknames,
room-specific poker `PlayerId` values, and seat numbers are never authorization identities. The
service returns only frozen snapshots. Its private room aggregate owns membership, settings,
pending seat requests, one authoritative `TableState`, and at most one private active `HoldemHand`.

The host starts hand number `N` only when `N` equals the room's `next_hand_number`. A successful
start consumes that number, moves the table button exactly once, and snapshots the eligible table
occupants through `HoldemHand.start()`. Player actions compare both the hand number and an action
sequence before mapping the acting `GuestId` to its captured `PlayerId`. Betting legality remains
entirely in the poker domain.

## Room and seating policy

Rooms are `OPEN`, `HAND_IN_PROGRESS`, or terminally `CLOSED`. Terminal domain hands are settled in
the same candidate transaction that produced them. Final stacks replace the participants' table
stacks without changing seat ownership, zero-stack players become sitting out, the active hand is
cleared, and the room returns to `OPEN`. The button remains the completed hand's anchor until the
next successful start moves it once.

Guests request a specific zero-based seat. With approval enabled, one request per guest and per
seat remains pending for explicit host approval or rejection. Disabling approval does not alter
existing requests. New requests seat immediately only while the room is open.

The table owns the stack while a member is seated. Standing between hands transfers the exact
stack into member-retained room state; reseating restores it. Leaving or being kicked discards the
room-local play stack and removes membership. A returning guest receives a new room-specific
`PlayerId` and the current default stack on their next first seating. There is no wallet, economy,
rebuy, or direct participant-stack mutation.

## Settings and secrets

Room names and nicknames use centralized Unicode NFKC normalization, collapsed whitespace, and
control-character rejection. Nicknames compare for uniqueness by normalized `casefold()`.

Small blind, big blind, and default starting stack may change only between hands. Room name,
password, and the seating-approval flag may change during a hand because they do not alter poker
state. Maximum capacity is fixed at six.

Room passwords are optional. The default hasher uses a random salt and PBKDF2-HMAC-SHA256 from the
Python standard library, and verification uses constant-time comparison. Plaintext is never stored.
Snapshots expose only whether password protection is enabled; they never expose salt, digest,
iteration count, `TableState`, poker `PlayerId`, or a hand aggregate. Viewer projections disclose a
participant's own hole cards only. Opponent cards remain concealed during play and after showdown;
host status grants no special visibility. Deck state, burns, settlement evaluations, and exact
best-five cards never enter application projections.

Eight-character room codes use `ABCDEFGHJKLMNPQRSTUVWXYZ23456789`, normalize to uppercase, and are
generated with `secrets`. Codes identify rooms but are not authentication. Closed-room codes remain
reserved while their rooms remain in memory.

## Atomicity and concurrency

Each service mutation authorizes the original room, copies its table and private hand execution
state into a candidate, mutates and validates only the candidate, builds the requested projection,
and replaces the repository entry only after success. Terminal settlement, table reconciliation,
active-hand clearing, and return to `OPEN` are one candidate transaction.

`InMemoryRoomRepository` uses a short process-local lock to make each primitive lookup, insertion,
and replacement safe across transport worker threads while keeping its two indexes reconciled.
Callers must still serialize the complete same-room load, copy, command, optional terminal
settlement, validation, and replace boundary. Phase 9B wraps that application transaction in one
process-local per-room async serializer shared by all transports. A future
multi-process deployment will require a different repository/concurrency design; no distributed
locks, Redis, or persistence are included here.

## Phase 11A transport disconnect and reconnect

Closing or losing a WebSocket removes only its connection-registry entry. It does not call Leave,
stand a player, remove membership or a seat, change a stack or host authority, or mutate an active
hand. The same guest token can bind again to the existing membership and receives a new projection
for that viewer. Leave, Kick, and Close Room remain explicit terminal room mutations with their
existing authorization rules. A guest who left or was kicked needs a new join; a closed room cannot
be rejoined. Multiple sockets for one guest remain permitted in 11A, and each receives that guest's
viewer-specific state. Socket replacement belongs to 11B.

There is no disconnect grace period or automatic standing in 11A. A disconnected seated player
remains eligible under the existing start-hand rules. An offline current actor can stall an active
hand because action deadlines and timeout actions belong to 11C. Reconnect is manual, and commands
are not replayed automatically.

## Phase 11B authoritative socket replacement

One room guest now has one authoritative WebSocket session. A successful same-token bind
installs the new session under the room coordinator lock, revokes the old session, and sends
the new socket a fresh viewer-specific state. The old socket closes with policy code 1008
and a fixed `session ended` reason. Commands from it are ignored, and its late cleanup
cannot remove the new binding. Leave, Kick, and Close Room still terminate membership or
the room through their explicit commands. Replacement itself changes no room or poker
state. Manual reconnect and the Phase 11A disconnect rules otherwise remain in effect.

## Phase 13E.1 action deadlines, pause, and time bank

RoomService records a configurable base deadline for each active turn using an injectable
clock. The allowed decision time is 5–120 seconds and defaults to 30 seconds. Monotonic
milliseconds decide expiry; a Unix millisecond timestamp is projected for display. A player
action arriving at or after the deadline loses to the server timeout.
The timeout checks when check is legal and folds otherwise, through the existing hand action
and settlement path. Reconnect and socket replacement do not change the deadline.

The realtime coordinator schedules one task for each active room turn. Each callback verifies
room, hand number, action sequence, actor, deadline revision, and actual expiry under the room
lock. A stale callback changes nothing. Task cancellation only cleans up resources; it does
not establish correctness. The scheduler and deadlines are process-local, so process restart
does not recover an in-progress hand.

The time bank is room-local and persistent across hands. Its total is configurable from
0–300 seconds. When the base deadline expires, the server enters the bank without spending it
up front. Only elapsed bank time is deducted when the player acts, pauses, or times out. The
base-to-bank transition advances the action sequence used for command stale-state protection,
so a command sent for the base deadline cannot act under the extended deadline. A zero balance
skips the bank and immediately checks or folds through the existing timeout path.

Refill is additive and capped by the configured total. After every configured number of that
player's completed hands, the configured amount is added at the next hand start. An amount of
zero disables automatic refill. Defaults are a 60-second total and +60 seconds every hand,
which preserves the earlier full-per-hand behavior. Saving a time-bank total, amount, or cadence
between hands resets every current member's balance to the configured total and restarts the
cadence counter.

Only the host can pause or resume, and only during an active hand. Pause is idempotent and
freezes the exact remaining base or bank time; gameplay actions are rejected while paused.
Reconnect and room commands continue. Resume is idempotent and creates a new deadline revision
from the frozen duration. At an exact deadline boundary, the due base/bank transition is applied
before pause. Stale pre-pause callbacks therefore cannot extend or time out the resumed turn.

The coordinator resolves all due transitions under its room lock before admitting a late
command or sending reconnect state. Old timer tasks are cancelled for cleanup; deadline
identity and the application balance decide correctness. Viewer snapshots include only the
current phase duration, current actor's bank remaining/total, bank-use flag, and refill cadence.
Paused projections omit the running deadline and expose the frozen remaining duration. The
browser uses those fields for display only; it never decides timeout outcomes. Reconnect and
socket replacement do not replenish a balance or extend a deadline.

## Phase 11F disconnect grace

An unexpected loss of the authoritative socket starts a process-local 60,000 millisecond grace
period for a seated member. Socket replacement, Leave, Kick, and Close Room do not start grace.
The current session identity decides whether disconnect cleanup may start grace; a replaced
socket's late cleanup cannot affect its successor. A same-token reconnect invalidates the pending
grace before its fresh viewer-specific state is sent. Grace revisions and monotonic expiry are
checked under the same room lock as commands. Task cancellation cleans resources but is not the
authority for seat changes.

If grace expires while the room is open, the disconnected member stands using the existing room
operation. Membership, host identity, and the exact retained stack remain. If a hand is active,
the member stays in the hand; seat cleanup waits until settlement and occurs only if the member
remains disconnected. Action deadlines and timebank continue independently throughout grace.
If a host approves a pending seat request after its member has disconnected, grace starts when
the offline member becomes seated, so that seat cannot remain occupied indefinitely.
Grace state and tasks exist only in this process; a restart does not recover them. Reconnect is
still manual, and no grace or scheduler identifiers appear in the wire projection.

## Phase 12C private Stand-Up lifecycle

An optional room-owned Stand-Up round starts from the PlayerIds and seats of the poker hand that
begins it. The round freezes its penalty and cohort. Later poker players may join the hand without
joining the active side-game round.

After authoritative Hold'em settlement, RoomService writes poker final stacks to its copied
candidate table. It adapts the validated pot-index-zero award to the pure Stand-Up hand outcome,
then advances the round using the candidate table's settled cohort stacks. A squid resolution
produces a capped transfer plan, which is checked and applied only to that candidate. The
completed-hand record remains poker-only; the private Stand-Up result separately explains any
additional stack transfers. Validation, viewer projection, and repository replacement follow the
entire transaction, so a failure commits neither poker settlement nor side-game payment.

An unresolved round cancels when a cohort member leaves, is kicked, vacates a poker seat, busts
at settlement, or the room closes. Transport disconnect alone has no side-game effect. Grace
cleanup uses the existing stand-up operation after hand settlement, so a completed side-game
resolution precedes any deferred seat cleanup. A new round can begin only when a later poker
hand starts.

## Phase 12D Stand-Up settings and realtime projection

RoomSettings now owns an explicit enable flag and a positive per-recipient chip penalty. The
existing host-only settings command can change either value and broadcasts its normal ACK followed
by viewer-specific STATE. A penalty change affects the next round; an already-active round retains
its frozen penalty. Disabling the game cancels an unresolved round with the domain's disabled
reason, including when a poker hand is still in progress.

Room snapshots and realtime DTOs expose only seat-oriented Stand-Up state: frozen participant
seats and cleared flags, terminal squid and transfer seats, payout totals, and cancellation reason.
Internal PlayerIds never cross the application projection. Same-token reconnect receives a fresh
authoritative projection, replaced sockets cannot issue the host command, and disconnect alone
does not alter a round. Frontend work in this phase is limited to strict Zod compatibility and
command construction; visible markers, controls, and payout presentation remain Phase 12E.

## Phase 13E.2 session stack accounting

The host may adjust a current member's established session stack only between hands. A rebuy
or top-up adds a positive external amount, a cash-out removes a positive external amount, and
a correction applies one explicit nonzero signed amount. The command compares both the next
hand number and current ledger sequence before mutation. Exact command-ID replays return the
current projection without appending or applying again; reusing an ID with different input is
rejected. The room coordinator continues to serialize this operation with every other command.

The first stack assigned to a member becomes that player's session starting stack. Each accepted
adjustment appends an immutable, monotonically sequenced room-local entry with display nickname,
seat at the time of adjustment, signed delta, resulting stack, host indicator/seat, and optional
normalized reason. Public ledger and summary projections never include PlayerId or GuestId.
Accounts and history are process-local and intentionally have no database persistence.

Session poker net is derived as `current stack - starting stack - external net`, where external
net is cumulative chips added minus cumulative chips removed. All three adjustment types affect
external net, so corrections do not masquerade as poker winnings. Poker and Stand-Up settlement
change the current stack without changing external totals, and each completed Hold'em hand
increments the participating accounts' hand count.

A full cash-out leaves seat occupancy and membership unchanged, sets the stack to zero, and marks
the occupant sitting out. A later positive host adjustment restores that occupied seat to sitting
in. Adjusting a standing member's retained stack keeps them standing. No adjustment silently
kicks, disconnects, seats, or stands a member.
