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
