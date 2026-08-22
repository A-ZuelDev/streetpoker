# Domain boundary

Future poker rules belong in this package as framework-independent Python. Domain code must not
import FastAPI, WebSockets, SQLAlchemy, PostgreSQL adapters, React, or frontend code.

The server is authoritative for cards, winners, turn order, legal actions, pots, and stack changes.
Concealed information must be filtered before any transport representation is created.

Phase 1 provides immutable card values and a standard deck that owns its remaining cards. Deck
construction, shuffling, and drawing stay independent of application and persistence code.

Card primitive serialization is explicit. The future server projection layer remains responsible
for deciding whether a card may be disclosed to a client.

Phase 2 adds the between-hand player, chip-stack, seat, and six-max table-state foundation. Player,
stack, seat-index, seated-player, and seat values are immutable. The table is the mutable aggregate
that atomically enforces unique occupancy and bounded dealer-button movement.

Seat indexes are zero-based and capacity-independent. Six-max tables accept indexes zero through
five, while larger nonnegative indexes remain valid values for future table capacities. Empty seats
remain distinct from sitting-out players. Sitting-in players require a positive stack; sitting-out
players may retain a zero stack.

Table state does not own cards, dealing, blinds, betting, pots, streets, hand evaluation, active-hand
lifecycle, network connections, serialization, or persistence concerns.

Phase 3 adds a separate aggregate for the pure mechanics of one no-limit betting round. The
aggregate owns its participant snapshots, commitments, remaining stacks, wager and minimum-raise
levels, player-relative action-reopening baselines, pending action, and clockwise turn order. Every
successful command atomically replaces a complete immutable snapshot; failed commands preserve the
prior snapshot.

Betting rounds begin with zero contributions and an explicitly supplied first actor. Blind posting,
street selection, cards, pots and side-pot construction, showdown, and full-hand lifecycle remain
outside this aggregate. Future preflop initialization will require generic forced contributions and
a nominal live wager level so that a short blind does not incorrectly reduce the amount to call.

Phase 4 adds pure construction of ordered pots from immutable cumulative hand contributions and
live-or-folded eligibility. The transformation retains authoritative committed amounts, identifies
any unique-highest unmatched excess separately, and reconciles every chip globally and per player.
Folded players continue to fund pots but cannot win them. Multiple contribution thresholds produce
the main pot followed by ascending side pots, without depending on betting-round history or whether
a live player has chips behind.

Pot construction does not aggregate street commitments, mutate stacks, validate betting-history
reachability, evaluate hands, select winners, award chips, distribute odd chips, divide pots across
boards, or apply rake. A future authoritative hand orchestrator must supply cumulative commitments
and map betting participants to the pot-specific live-or-folded eligibility state.

Phase 5 adds `HoldemHand`, the authoritative pure-domain aggregate for one no-limit Texas Hold'em
hand from table snapshot through fold completion or showdown readiness. Starting a hand snapshots
eligible sitting-in positive-stack occupants and the current eligible button. The hand then owns its
participant stacks, private shuffled deck, private hole cards, private burn pile, public board,
street state, and current betting-round candidate. It never mutates `TableState`; a later settlement
layer will apply awarded final stacks between hands.

Every participant obeys one accounting equation throughout the hand:

`starting_stack = current_stack + gross_committed - returned_excess`

Gross commitments are cumulative and monotonic. Street commitments reset between betting rounds.
Returned excess is zero until terminal pot construction; unique uncalled excess is then restored to
the participant's current stack while gross commitment remains unchanged for audit and replay.
Contestable commitment is `gross_committed - returned_excess`.

StreetPoker Phase 5 uses an explicit short-blind policy. With three or more participants, a short
all-in big blind does not reduce the nominal preflop live wager or minimum raise increment. At
50/100 with a big blind posting only 60, the first call target is 100 and the first full raise-to is
200, while pot accounting retains the actual 60 post. Heads-up, when the big blind is all-in short,
the live target is the highest actual blind commitment. A small blind posting 50 against a 60 big
blind may call 10 or fold but cannot raise without an active opponent. If the big blind posts only
30 against a 50 small blind, no action remains; terminal pot construction returns the unmatched
small-blind excess. The nominal big blind remains the minimum raise increment. This is a fixed
StreetPoker policy, not a configurable poker-room rules framework.

`BettingRound` remains responsible for checks, calls, bets, raises, folds, minimum raises, reopening,
and turn order. Its generic initial commitments and initial live wager represent forced live posts
without blind-specific logic. `HoldemHand` reconciles only round deltas into cumulative state,
propagates fold/all-in status, deals later streets, and creates no new round when fewer than two
players can bet. A lone player still facing an all-in wager retains a call-or-fold decision, but no
player may create an uncontested aggressive wager against only all-in opponents.

All aggregate transitions use narrowly scoped copy-on-write: an independent deck and betting-round
candidate are mutated, reconciled, and validated before replacing authoritative state. Mutable
`Deck` and `BettingRound` instances are never exposed by `HoldemHand`. Its immutable snapshot is
trusted server-domain state, not a client serializer. Burn identities remain private inside the
aggregate; the snapshot exposes only their count. Future viewer projections must authorize every
private card independently rather than relying on UI hiding.

Phase 5 stops at `SHOWDOWN_READY` or `COMPLETE_BY_FOLD`. It constructs pots and refunds uncalled
excess but does not evaluate hands, select winners, award pots, split ties, assign odd chips, apply
rake, serialize clients, persist state, or implement variants and alternate blind structures.
