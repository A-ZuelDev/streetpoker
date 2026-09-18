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

## Phase 11C action deadlines

RoomService records a 30,000 millisecond deadline for each active turn using an injectable
clock. Monotonic milliseconds decide expiry; a Unix millisecond timestamp is projected for
future display. A player action arriving at or after the deadline loses to the server timeout.
The timeout checks when check is legal and folds otherwise, through the existing hand action
and settlement path. Reconnect and socket replacement do not change the deadline.

The realtime coordinator schedules one task for each active room turn. Each callback verifies
room, hand number, action sequence, actor, deadline revision, and actual expiry under the room
lock. A stale callback changes nothing. Task cancellation only cleans up resources; it does
not establish correctness. The scheduler and deadlines are process-local, so process restart
does not recover an in-progress hand. There is no frontend countdown, timebank, or automatic
reconnect in this phase.

## Phase 11E automatic per-hand timebank

Each hand gives each participant 60,000 milliseconds of process-local timebank. An early
action keeps that balance for later turns in the same hand. At the 30,000 millisecond base
deadline, RoomService consumes the actor's full remaining balance and commits an extended
deadline measured from the original deadline. This transition advances the action sequence
used for command stale-state protection, so a command sent for the base deadline cannot act
under the extended deadline. A fresh state lets the player act during the extension. An
action does not refund consumed timebank. When no balance remains, deadline expiry checks
or folds through the existing action path. Settlement discards the per-hand balances, and
the next hand creates fresh balances for its participants.

The coordinator resolves all due transitions under its room lock before admitting a late
command or sending reconnect state. Old timer tasks are cancelled for cleanup; deadline
identity and the application balance decide correctness. Viewer snapshots include only the
current actor's remaining milliseconds and whether that actor is using timebank. The browser
uses those fields and the projected Unix deadline for display only. Reconnect and socket
replacement do not replenish a balance or extend a deadline.

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
