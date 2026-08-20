# Domain boundary

Future poker rules belong in this package as framework-independent Python. Domain code must not
import FastAPI, WebSockets, SQLAlchemy, PostgreSQL adapters, React, or frontend code.

The server is authoritative for cards, winners, turn order, legal actions, pots, and stack changes.
Concealed information must be filtered before any transport representation is created.

Phase 1 begins with immutable card values and a standard deck that owns its remaining cards.
Deck construction, shuffling, and drawing stay independent of application and persistence code.

Card primitive serialization is explicit. The future server projection layer remains responsible
for deciding whether a card may be disclosed to a client.
