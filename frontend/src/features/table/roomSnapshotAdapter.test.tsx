import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import {
  activeRoomSnapshot,
  completedRoomSnapshot,
  openRoomSnapshot,
} from '../../test/roomSnapshots';
import { TableScreen } from './TableScreen';
import { roomSnapshotToTableView } from './roomSnapshotAdapter';

describe('roomSnapshotToTableView', () => {
  it('maps all six seats and permits an unseated host', () => {
    const table = roomSnapshotToTableView(openRoomSnapshot(), 'guest_host');

    expect(table.seats).toHaveLength(6);
    expect(
      table.seats.some((seat) => seat.kind === 'occupied' && seat.isHero),
    ).toBe(false);
    expect(table).toMatchObject({
      mode: 'live',
      variant: 'open',
      isHandActive: false,
      roomName: 'Friday Night',
      roomCode: 'ABCDEFGH',
      pot: 0,
      board: [],
      legalActions: null,
      chat: null,
    });

    render(<TableScreen table={table} connectionStatus="connected" />);
    expect(screen.getByText('Not seated')).toBeInTheDocument();
    expect(screen.getByText('Watching the table')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /fold/i })).toBeNull();
    expect(document.body.textContent).not.toContain('internal-room-id');
    expect(document.body.textContent).not.toContain('guest_host');
  });

  it('maps authoritative cards, board, pot, markers, and contributions', () => {
    const table = roomSnapshotToTableView(activeRoomSnapshot(), 'guest_host');
    const hero = table.seats[0];
    const opponent = table.seats[1];
    const nonparticipant = table.seats[2];

    expect(table.street).toBe('Flop');
    expect(table.pot).toBe(250);
    expect(table.board).toEqual([
      { rank: 'A', suit: 'spades' },
      { rank: '10', suit: 'hearts' },
      { rank: '2', suit: 'clubs' },
    ]);
    expect(hero).toMatchObject({
      kind: 'occupied',
      isHero: true,
      isActing: true,
      contribution: 50,
      blind: 'small-blind',
      cards: [
        { rank: 'K', suit: 'spades' },
        { rank: 'Q', suit: 'spades' },
      ],
    });
    expect(opponent).toMatchObject({
      kind: 'occupied',
      state: 'all-in',
      contribution: 100,
      blind: 'big-blind',
      cards: 'concealed',
    });
    expect(nonparticipant).toMatchObject({
      kind: 'occupied',
      state: 'not-in-hand',
      contribution: null,
      cards: null,
      isDealer: true,
    });
  });

  it('conceals an opponent even if an invalid upstream snapshot supplies cards', () => {
    const snapshot = activeRoomSnapshot();
    snapshot.active_hand!.players[1]!.hole_cards = [
      { rank: 'ace', suit: 'diamonds' },
      { rank: 'ace', suit: 'clubs' },
    ];

    const table = roomSnapshotToTableView(snapshot, 'guest_host');

    expect(table.seats[1]).toMatchObject({ cards: 'concealed' });
  });

  it('does not fabricate cards for a spectator or nonparticipant', () => {
    const table = roomSnapshotToTableView(
      activeRoomSnapshot(),
      'guest_spectator',
    );

    expect(
      table.seats.filter(
        (seat) => seat.kind === 'occupied' && Array.isArray(seat.cards),
      ),
    ).toEqual([]);
    expect(table.seats[2]).toMatchObject({ cards: null });
  });

  it('does not render completed hand data as current table state', () => {
    const table = roomSnapshotToTableView(
      completedRoomSnapshot(),
      'guest_host',
    );

    expect(table).toMatchObject({
      variant: 'open',
      isHandActive: false,
      street: 'Open table',
      pot: 0,
      board: [],
    });
  });
});
