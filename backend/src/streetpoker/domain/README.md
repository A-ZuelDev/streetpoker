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
