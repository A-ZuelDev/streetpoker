# Room application boundary

Phase 8 provides process-local coordination for private six-seat rooms. Code in this package is
independent of FastAPI, WebSockets, SQLAlchemy, PostgreSQL, and frontend transport models.

`RoomService` is the public command boundary. Every mutation receives a `GuestId`; nicknames,
room-specific poker `PlayerId` values, and seat numbers are never authorization identities. The
service returns only frozen snapshots. Its private room aggregate owns membership, settings,
pending seat requests, the guest-to-player mapping, and one authoritative `TableState`.

## Room and seating policy

Rooms are `OPEN`, `HAND_IN_PROGRESS`, or terminally `CLOSED`. Phase 8 creates only open rooms and
can close them; it intentionally has no public transition into a hand. The active-hand status is
present so unsafe future lifecycle mutations already have explicit guards.

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
iteration count, `TableState`, poker `PlayerId`, a hand aggregate, or cards.

Eight-character room codes use `ABCDEFGHJKLMNPQRSTUVWXYZ23456789`, normalize to uppercase, and are
generated with `secrets`. Codes identify rooms but are not authentication. Closed-room codes remain
reserved while their rooms remain in memory.

## Atomicity and concurrency

Each service mutation authorizes the original room, copies it into a candidate, mutates and
validates the candidate, and replaces the repository entry only after success. Phase 8 table copies
are reconstructed from public immutable seat state. The dealer button must remain unset because no
Phase 8 operation starts gameplay or moves it; Phase 9 must revisit table cloning when it introduces
the complete hand lifecycle.

`InMemoryRoomRepository` atomically maintains room-ID and normalized-code indexes, but it is not
thread-safe. Phase 8 assumes callers serialize commands. Phase 9 must wrap the complete
read/candidate/replace transaction in per-room serialization shared by all transports. A future
multi-process deployment will require a different repository/concurrency design; no locks, Redis,
or persistence are included here.
