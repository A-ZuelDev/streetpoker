import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

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

  it('offers an unseated member exact seat actions without optimistic seating', () => {
    const snapshot = openRoomSnapshot();
    snapshot.room.members[1]!.status = 'in_room';
    snapshot.room.members[1]!.stack = null;
    snapshot.room.seats[1] = {
      seat_index: 1,
      guest_id: null,
      nickname: null,
      stack: null,
    };
    const onRoomCommand = vi.fn(() => true);
    render(
      <TableScreen
        table={roomSnapshotToTableView(snapshot, 'guest_alice')}
        connectionStatus="connected"
        onRoomCommand={onRoomCommand}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Request seat 1' }));
    expect(onRoomCommand).toHaveBeenCalledWith({
      type: 'request_seat',
      seatIndex: 0,
    });
    expect(
      screen.getByRole('button', { name: 'Request seat 1' }),
    ).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('guest_alice');
    expect(screen.queryByText('Room settings')).toBeNull();
  });

  it('uses Take seat for approval-disabled rooms and disables requested seats', () => {
    const snapshot = openRoomSnapshot();
    snapshot.room.settings.seating_approval_required = false;
    snapshot.room.members[1]!.status = 'in_room';
    snapshot.room.members[1]!.stack = null;
    snapshot.room.seats[1] = {
      seat_index: 1,
      guest_id: null,
      nickname: null,
      stack: null,
    };
    snapshot.room.seat_requests = [
      { guest_id: 'guest_other', nickname: 'Ember', seat_index: 0 },
    ];
    render(
      <TableScreen
        table={roomSnapshotToTableView(snapshot, 'guest_alice')}
        connectionStatus="connected"
      />,
    );
    expect(screen.getByRole('button', { name: 'Take seat 2' })).toBeEnabled();
    expect(
      screen.getByRole('button', { name: 'Seat 1 requested' }),
    ).toBeDisabled();
  });

  it('renders host-only request and moderation controls using internal targets', () => {
    const snapshot = openRoomSnapshot();
    snapshot.room.seat_requests = [
      { guest_id: 'guest_requester_private', nickname: 'Ember', seat_index: 2 },
    ];
    snapshot.room.members.push({
      guest_id: 'guest_requester_private',
      nickname: 'Ember',
      status: 'in_room',
      is_host: false,
      stack: null,
    });
    const onRoomCommand = vi.fn(() => true);
    render(
      <TableScreen
        table={roomSnapshotToTableView(snapshot, 'guest_host')}
        connectionStatus="connected"
        onRoomCommand={onRoomCommand}
      />,
    );
    const requestSection = screen
      .getByRole('heading', { name: 'Seat requests' })
      .closest('section')!;
    fireEvent.click(
      within(requestSection).getByRole('button', { name: 'Approve' }),
    );
    expect(onRoomCommand).toHaveBeenCalledWith({
      type: 'approve_seat',
      targetGuestId: 'guest_requester_private',
    });
    expect(document.body.textContent).not.toContain('guest_requester_private');
    expect(document.body.innerHTML).not.toContain('guest_requester_private');
    expect(
      screen.getByRole('heading', { name: 'Room settings' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('heading', { name: 'Host controls' }),
    ).toBeInTheDocument();
  });

  it('derives host authority only from host_guest_id', () => {
    const snapshot = openRoomSnapshot();
    snapshot.room.members[1]!.is_host = true;
    render(
      <TableScreen
        table={roomSnapshotToTableView(snapshot, 'guest_alice')}
        connectionStatus="connected"
      />,
    );
    expect(screen.queryByRole('heading', { name: 'Host controls' })).toBeNull();
    expect(screen.queryByRole('heading', { name: 'Room settings' })).toBeNull();
    expect(
      screen.getByRole('heading', { name: 'Player controls' }),
    ).toBeInTheDocument();
  });

  it('blocks poker actions while a room command is pending', () => {
    const table = roomSnapshotToTableView(
      activeRoomSnapshot(),
      'guest_host',
      'connected',
      null,
      {
        commandId: 'room',
        type: 'reject_seat',
        targetGuestId: 'guest_other',
        acknowledged: false,
      },
    );
    render(
      <TableScreen
        table={table}
        connectionStatus="connected"
        onPokerAction={() => true}
      />,
    );
    expect(screen.getByRole('button', { name: 'Fold' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Call 50' })).toBeDisabled();
  });
});
