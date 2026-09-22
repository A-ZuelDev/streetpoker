import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { activeRoomSnapshot, openRoomSnapshot } from '../../test/roomSnapshots';
import { activeDemoTable, openDemoTable } from './demoTable.fixture';
import { roomSnapshotToTableView } from './roomSnapshotAdapter';
import { TableScreen } from './TableScreen';

describe('TableScreen', () => {
  it('shows a display-only timer that reaches zero without sending an action', () => {
    vi.useFakeTimers();
    vi.setSystemTime(1_799_999_970_000);
    try {
      const onPokerAction = vi.fn(() => true);
      const view = render(
        <TableScreen
          table={roomSnapshotToTableView(activeRoomSnapshot(), 'guest_host')}
          connectionStatus="connected"
          onPokerAction={onPokerAction}
        />,
      );
      const timer = screen.getByRole('timer', {
        name: 'Authoritative action timer and time bank',
      });
      expect(timer).toHaveTextContent('30s');

      act(() => vi.advanceTimersByTime(1_050));
      expect(timer).toHaveTextContent('29s');
      act(() => vi.advanceTimersByTime(30_000));
      expect(timer).toHaveTextContent('0s');
      expect(timer).toHaveTextContent('Awaiting table update');
      expect(onPokerAction).not.toHaveBeenCalled();
      expect(screen.getByRole('button', { name: 'Fold' })).toBeEnabled();

      view.unmount();
      expect(vi.getTimerCount()).toBe(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it('hides the ticking value while stale and restarts from a fresh deadline', () => {
    vi.useFakeTimers();
    vi.setSystemTime(1_799_999_970_000);
    try {
      const snapshot = activeRoomSnapshot();
      const view = render(
        <TableScreen
          table={roomSnapshotToTableView(snapshot, 'guest_host')}
          connectionStatus="connected"
        />,
      );
      expect(screen.getByRole('timer')).toHaveTextContent('30s');
      expect(vi.getTimerCount()).toBe(1);

      view.rerender(
        <TableScreen
          table={roomSnapshotToTableView(
            snapshot,
            'guest_host',
            'disconnected',
          )}
          connectionStatus="disconnected"
        />,
      );
      expect(screen.queryByRole('timer')).toBeNull();
      expect(screen.getByText('Waiting for fresh state')).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Fold' })).toBeNull();
      expect(vi.getTimerCount()).toBe(0);

      act(() => vi.advanceTimersByTime(10_000));
      view.rerender(
        <TableScreen
          table={roomSnapshotToTableView(snapshot, 'guest_host', 'syncing')}
          connectionStatus="syncing"
        />,
      );
      expect(screen.queryByRole('timer')).toBeNull();
      expect(screen.getByText('Waiting for fresh state')).toBeInTheDocument();

      const fresh = structuredClone(snapshot);
      view.rerender(
        <TableScreen
          table={roomSnapshotToTableView(fresh, 'guest_host')}
          connectionStatus="connected"
        />,
      );
      expect(screen.getByRole('timer')).toHaveTextContent('20s');
      expect(vi.getTimerCount()).toBe(1);

      fresh.active_hand!.action_sequence += 1;
      fresh.active_hand!.action_deadline_unix_ms! += 20_000;
      view.rerender(
        <TableScreen
          table={roomSnapshotToTableView(fresh, 'guest_host')}
          connectionStatus="connected"
        />,
      );
      expect(screen.getByRole('timer')).toHaveTextContent('40s');
      expect(vi.getTimerCount()).toBe(1);

      view.rerender(
        <TableScreen
          table={roomSnapshotToTableView(openRoomSnapshot(), 'guest_host')}
          connectionStatus="connected"
        />,
      );
      expect(screen.queryByRole('timer')).toBeNull();
      expect(vi.getTimerCount()).toBe(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it('shows timebank only after an authoritative fresh state activates it', () => {
    vi.useFakeTimers();
    vi.setSystemTime(1_799_999_970_000);
    try {
      const base = activeRoomSnapshot();
      const view = render(
        <TableScreen
          table={roomSnapshotToTableView(base, 'guest_host')}
          connectionStatus="connected"
        />,
      );
      act(() => vi.advanceTimersByTime(30_000));
      expect(screen.getByRole('timer')).toHaveTextContent('0s');
      expect(screen.getByRole('timer')).toHaveTextContent(
        'Awaiting table update',
      );
      expect(screen.queryByText('Time bank')).toBeNull();

      const extended = structuredClone(base);
      extended.active_hand!.action_sequence += 1;
      extended.active_hand!.action_deadline_unix_ms! += 60_000;
      extended.active_hand!.current_actor_timebank_ms = 60_000;
      extended.active_hand!.current_actor_using_timebank = true;
      view.rerender(
        <TableScreen
          table={roomSnapshotToTableView(extended, 'guest_host', 'syncing')}
          connectionStatus="syncing"
        />,
      );
      expect(screen.queryByRole('timer')).toBeNull();
      expect(screen.queryByText('Time bank')).toBeNull();
      view.rerender(
        <TableScreen
          table={roomSnapshotToTableView(extended, 'guest_host')}
          connectionStatus="connected"
        />,
      );
      expect(
        screen.getByRole('timer', {
          name: 'Authoritative action timer and time bank',
        }),
      ).toHaveTextContent('60s');
      expect(screen.getByText('Time bank')).toBeInTheDocument();
      view.unmount();
      expect(vi.getTimerCount()).toBe(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it('uses coherent contribution, call, and wager totals in the active demo', () => {
    const hero = activeDemoTable.seats.find(
      (seat) => seat.kind === 'occupied' && seat.isHero,
    );
    const legalActions = activeDemoTable.legalActions;
    const highestContribution = Math.max(
      ...activeDemoTable.seats.map((seat) =>
        seat.kind === 'occupied' ? (seat.contribution ?? 0) : 0,
      ),
    );

    if (hero === undefined || hero.contribution === null) {
      throw new Error('The active demo requires a contributing hero.');
    }

    expect(highestContribution).toBe(600);
    expect(hero.contribution).toBe(250);
    expect(legalActions.call).toEqual({ chips: 350, total: 600 });
    expect(hero.contribution + legalActions.call.chips).toBe(
      legalActions.call.total,
    );
    expect(legalActions.wager).toMatchObject({
      minimum: 1_200,
      maximum: 8_100,
    });
    expect(hero.contribution + hero.stack).toBe(legalActions.wager.maximum);
  });

  it('renders all six active-table seats and privacy-safe poker information', () => {
    const { container } = render(
      <TableScreen table={activeDemoTable} backendStatus="online" />,
    );

    expect(container.querySelectorAll('[data-seat-index]')).toHaveLength(6);
    expect(screen.getByLabelText('Seat 1: Mara')).toBeInTheDocument();
    expect(screen.getByText('You · Seat 1')).toBeInTheDocument();
    expect(
      screen.getByRole('img', { name: 'A of spades' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('img', { name: 'K of hearts' }),
    ).toBeInTheDocument();
    expect(screen.getAllByRole('img', { name: 'Concealed card' })).toHaveLength(
      8,
    );

    const board = screen.getByLabelText('Community board');
    expect(board.querySelectorAll('.playing-card')).toHaveLength(5);
    expect(
      within(board).getByRole('img', { name: 'Q of clubs' }),
    ).toBeInTheDocument();
    expect(
      within(board).getByRole('img', { name: '9 of diamonds' }),
    ).toBeInTheDocument();
    expect(within(board).getByText('Q')).toHaveClass('playing-card__rank');
    expect(
      within(board)
        .getByRole('img', { name: 'Q of clubs' })
        .querySelector('.playing-card__suit'),
    ).not.toBeEmptyDOMElement();
    expect(screen.getByText('Turn')).toBeInTheDocument();
    expect(screen.getByLabelText('Pot 4,850')).toBeInTheDocument();
    expect(
      screen.getByLabelText('Mara current contribution 250'),
    ).toHaveTextContent('250');
    expect(
      screen.getByLabelText('Vale current contribution 600'),
    ).toHaveTextContent('600');
    expect(
      screen
        .getByLabelText('Mara current contribution 250')
        .querySelector('.chip-stack'),
    ).toBeInTheDocument();
    const felt = screen.getByTestId('poker-felt');
    expect(felt.querySelectorAll('[data-seat-contribution]')).toHaveLength(5);
    expect(screen.getByTestId('hero-hole-cards')).toBeInTheDocument();
    expect(screen.getByTestId('hero-player-pod')).toBeInTheDocument();

    expect(screen.getByTitle('Dealer')).toHaveTextContent('D');
    expect(screen.getByTitle('Small blind')).toHaveTextContent('SB');
    expect(screen.getByTitle('Big blind')).toHaveTextContent('BB');
    expect(screen.getAllByText('Your turn').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Folded').length).toBeGreaterThan(0);
    expect(screen.getAllByText('All in').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Sitting out').length).toBeGreaterThan(0);

    expect(screen.getByRole('button', { name: 'Fold' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Call 350' })).toBeDisabled();
    expect(screen.getByLabelText('Bet or raise amount')).toHaveValue(2_400);
    expect(screen.getByLabelText('Bet or raise amount slider')).toHaveValue(
      '2400',
    );
    expect(screen.getByRole('button', { name: 'Raise 2,400' })).toBeDisabled();
    expect(
      screen.getByRole('heading', { name: 'Members' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Collapse room panel' }),
    ).toHaveAttribute('aria-expanded', 'true');

    expect(container).not.toHaveTextContent('PlayerId');
    expect(container).not.toHaveTextContent('RoomId');
    expect(container).not.toHaveTextContent('guest_id');
  });

  it('collapses and expands each room panel section independently', () => {
    render(<TableScreen table={activeDemoTable} backendStatus="online" />);

    fireEvent.click(
      screen.getByRole('button', { name: 'Collapse Members section' }),
    );
    expect(screen.queryByRole('list', { name: 'Room members' })).toBeNull();
    expect(
      screen.getByRole('button', { name: 'Expand Members section' }),
    ).toHaveAttribute('aria-expanded', 'false');

    expect(
      screen.getByRole('button', { name: 'Expand Seat requests section' }),
    ).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(
      screen.getByRole('button', { name: 'Expand Seat requests section' }),
    );
    expect(screen.getByText('No pending requests')).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: 'Collapse Seat requests section' }),
    );
    expect(screen.queryByText('No pending requests')).toBeNull();

    fireEvent.click(
      screen.getByRole('button', { name: 'Collapse Chat preview section' }),
    );
    const chat = screen.getByRole('region', { name: 'Chat preview' });
    expect(within(chat).queryByText('nice hand')).toBeNull();
    expect(within(chat).queryByRole('textbox')).toBeNull();
    fireEvent.click(
      screen.getByRole('button', { name: 'Expand Chat preview section' }),
    );
    expect(
      within(chat).getByRole('textbox', { name: 'Chat message' }),
    ).toBeInTheDocument();

    expect(screen.queryByRole('button', { name: 'Room settings' })).toBeNull();
    expect(
      screen.getByRole('button', { name: 'Expand Host controls section' }),
    ).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(
      screen.getByRole('button', { name: 'Expand Host controls section' }),
    );
    expect(
      screen.getByRole('button', { name: 'Room settings' }),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: 'Collapse Host controls section' }),
    );
    expect(screen.queryByRole('button', { name: 'Room settings' })).toBeNull();
  });

  it('collapses and expands the full room panel', () => {
    render(<TableScreen table={activeDemoTable} backendStatus="online" />);

    fireEvent.click(
      screen.getByRole('button', { name: 'Collapse room panel' }),
    );

    expect(
      screen.getByRole('button', { name: 'Expand room panel' }),
    ).toHaveAttribute('aria-expanded', 'false');
    expect(
      screen.queryByRole('heading', { name: 'Members' }),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Expand room panel' }));

    expect(
      screen.getByRole('heading', { name: 'Members' }),
    ).toBeInTheDocument();
  });

  it('keeps room-command feedback visible when the room panel is closed', () => {
    render(
      <TableScreen
        table={roomSnapshotToTableView(activeRoomSnapshot(), 'guest_alice')}
        connectionStatus="connected"
        roomCommandError="That room action is unavailable."
      />,
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Collapse room panel' }),
    );
    expect(screen.getByRole('alert')).toHaveTextContent(
      'That room action is unavailable.',
    );
  });

  it('keeps connection truth separate from persistence warnings and stacks notices', () => {
    const { container } = render(
      <TableScreen
        table={roomSnapshotToTableView(openRoomSnapshot(), 'guest_host')}
        connectionStatus="connected"
        connectionError="An old connection warning."
        persistenceWarning
        roomCommandError="That room action is unavailable."
      />,
    );

    expect(screen.getByText('Table live')).toBeInTheDocument();
    expect(
      screen.queryByText(/Disconnected\. The displayed table is stale/),
    ).toBeNull();
    expect(screen.queryByText('An old connection warning.')).toBeNull();
    const notices = container.querySelector('.table-notices');
    expect(notices).not.toBeNull();
    expect(notices!.querySelectorAll('.session-banner')).toHaveLength(2);
    expect(notices!.children[0]).toHaveTextContent(
      'Your browser session will not persist',
    );
    expect(notices!.children[1]).toHaveTextContent(
      'That room action is unavailable.',
    );
  });

  it('copies only the visible room code and reports success', async () => {
    const originalClipboard = Object.getOwnPropertyDescriptor(
      navigator,
      'clipboard',
    );
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });
    try {
      render(
        <TableScreen
          table={roomSnapshotToTableView(openRoomSnapshot(), 'guest_host')}
          connectionStatus="connected"
        />,
      );
      fireEvent.click(screen.getByRole('button', { name: 'Copy code' }));
      await waitFor(() => expect(writeText).toHaveBeenCalledWith('ABCDEFGH'));
      expect(screen.getByText('Copied')).toBeInTheDocument();
      expect(screen.getByText('ABCDEFGH')).toBeInTheDocument();
      expect(screen.queryByRole('link', { name: 'StreetPoker' })).toBeNull();
    } finally {
      if (originalClipboard === undefined) {
        Reflect.deleteProperty(navigator, 'clipboard');
      } else {
        Object.defineProperty(navigator, 'clipboard', originalClipboard);
      }
    }
  });

  it('leaves the room code visible when clipboard copying fails', async () => {
    const originalClipboard = Object.getOwnPropertyDescriptor(
      navigator,
      'clipboard',
    );
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: vi.fn().mockRejectedValue(new Error('blocked')) },
    });
    try {
      render(
        <TableScreen
          table={roomSnapshotToTableView(openRoomSnapshot(), 'guest_host')}
          connectionStatus="connected"
        />,
      );
      fireEvent.click(screen.getByRole('button', { name: 'Copy code' }));
      expect(await screen.findByText('Copy unavailable')).toBeInTheDocument();
      expect(screen.getByText('ABCDEFGH')).toBeInTheDocument();
    } finally {
      if (originalClipboard === undefined) {
        Reflect.deleteProperty(navigator, 'clipboard');
      } else {
        Object.defineProperty(navigator, 'clipboard', originalClipboard);
      }
    }
  });

  it('starts with the overlay room panel closed below its breakpoint', () => {
    vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches: true }));
    try {
      render(<TableScreen table={activeDemoTable} backendStatus="online" />);
      expect(
        screen.getByRole('button', { name: 'Expand room panel' }),
      ).toHaveAttribute('aria-expanded', 'false');
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('renders accessible demo chat and appends a local-only message', () => {
    render(<TableScreen table={activeDemoTable} backendStatus="online" />);

    const chat = screen.getByRole('region', { name: 'Chat preview' });
    expect(within(chat).getByText('nice hand')).toBeInTheDocument();
    expect(within(chat).getByText('one more orbit?')).toBeInTheDocument();
    expect(within(chat).getByText('gl')).toBeInTheDocument();

    const input = within(chat).getByRole('textbox', { name: 'Chat message' });
    const send = within(chat).getByRole('button', { name: 'Send' });
    expect(send).toBeDisabled();

    fireEvent.change(input, { target: { value: 'good luck' } });
    expect(send).toBeEnabled();
    fireEvent.click(send);

    expect(within(chat).getByText('good luck')).toBeInTheDocument();
    expect(within(chat).getByText('You')).toBeInTheDocument();
    expect(input).toHaveValue('');
  });

  it('synchronizes the demo wager input, slider, minimum, and maximum', () => {
    render(<TableScreen table={activeDemoTable} backendStatus="online" />);

    const amount = screen.getByLabelText('Bet or raise amount');
    const slider = screen.getByLabelText('Bet or raise amount slider');

    fireEvent.change(amount, { target: { value: '' } });
    expect(amount).toHaveValue(null);

    for (const value of ['3', '32', '320', '3200']) {
      fireEvent.change(amount, { target: { value } });
      expect(amount).toHaveValue(Number(value));
    }

    expect(slider).toHaveValue('3200');
    expect(screen.getByRole('button', { name: 'Raise 3,200' })).toBeDisabled();

    fireEvent.change(amount, { target: { value: '' } });
    fireEvent.change(amount, { target: { value: '3' } });
    fireEvent.blur(amount);
    expect(amount).toHaveValue(1_200);
    expect(slider).toHaveValue('1200');

    fireEvent.change(amount, { target: { value: '' } });
    for (const value of ['9', '90', '900', '9000']) {
      fireEvent.change(amount, { target: { value } });
    }
    expect(amount).toHaveValue(9_000);
    fireEvent.keyDown(amount, { key: 'Enter' });
    expect(amount).toHaveValue(8_100);
    expect(slider).toHaveValue('8100');

    fireEvent.change(slider, { target: { value: '3200' } });
    expect(amount).toHaveValue(3_200);
    expect(slider).toHaveValue('3200');

    fireEvent.click(screen.getByRole('button', { name: 'Min 1,200' }));
    expect(amount).toHaveValue(1_200);
    expect(slider).toHaveValue('1200');

    fireEvent.click(screen.getByRole('button', { name: 'Max 8,100' }));
    expect(amount).toHaveValue(8_100);
    expect(slider).toHaveValue('8100');

    fireEvent.change(amount, { target: { value: '' } });
    fireEvent.change(amount, { target: { value: '9000' } });
    fireEvent.blur(amount);
    expect(amount).toHaveValue(8_100);
    expect(slider).toHaveValue('8100');
    expect(screen.getByRole('button', { name: 'Raise 8,100' })).toBeDisabled();
  });

  it('rebases a local wager draft when authoritative actions change', () => {
    const view = render(
      <TableScreen table={activeDemoTable} backendStatus="online" />,
    );
    const amount = screen.getByLabelText('Bet or raise amount');
    const slider = screen.getByLabelText('Bet or raise amount slider');

    fireEvent.change(amount, { target: { value: '' } });
    for (const value of ['3', '32', '320', '3200']) {
      fireEvent.change(amount, { target: { value } });
    }

    expect(amount).toHaveValue(3_200);
    expect(slider).toHaveValue('3200');
    expect(screen.getByRole('button', { name: 'Raise 3,200' })).toBeDisabled();

    view.rerender(
      <TableScreen table={{ ...activeDemoTable }} backendStatus="online" />,
    );
    expect(screen.getByLabelText('Bet or raise amount')).toHaveValue(3_200);

    if (activeDemoTable.legalActions === null) {
      throw new Error('The active demo requires legal actions.');
    }

    view.rerender(
      <TableScreen
        table={{
          ...activeDemoTable,
          legalActions: {
            ...activeDemoTable.legalActions,
            wager: {
              kind: 'raise',
              minimum: 4_000,
              maximum: 8_100,
              initial: 4_500,
            },
          },
        }}
        backendStatus="online"
      />,
    );

    expect(screen.getByLabelText('Bet or raise amount')).toHaveValue(4_500);
    expect(screen.getByLabelText('Bet or raise amount slider')).toHaveValue(
      '4500',
    );
    expect(screen.getByRole('button', { name: 'Raise 4,500' })).toBeDisabled();
  });

  it('renders the open demo with empty seats, a request, and host start state', () => {
    render(<TableScreen table={openDemoTable} backendStatus="offline" />);

    expect(
      screen.getByRole('button', { name: 'Request empty seat 3' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Request empty seat 5' }),
    ).toBeInTheDocument();
    expect(screen.getAllByText('Open seat')).toHaveLength(2);
    expect(
      screen.getByRole('heading', { name: 'Seat requests' }),
    ).toBeInTheDocument();
    expect(screen.getAllByText('Ember')).toHaveLength(2);
    expect(screen.getByText('Seat 3')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reject' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Start hand' })).toBeDisabled();
    expect(screen.getByText('Backend offline')).toBeInTheDocument();
  });

  it('announces backend and live connection transitions from the top-bar status only', () => {
    const view = render(
      <TableScreen table={openDemoTable} backendStatus="checking" />,
    );

    expect(screen.getByText('Backend checking')).toHaveAttribute(
      'role',
      'status',
    );
    view.rerender(<TableScreen table={openDemoTable} backendStatus="online" />);
    expect(screen.getByText('Backend online')).toHaveAttribute(
      'role',
      'status',
    );
    view.rerender(
      <TableScreen table={openDemoTable} backendStatus="offline" />,
    );
    expect(screen.getByText('Backend offline')).toHaveAttribute(
      'role',
      'status',
    );

    const liveTable = {
      ...openDemoTable,
      mode: 'live' as const,
      chat: null,
      legalActions: null,
      roomPanel: { ...openDemoTable.roomPanel, canStartHand: false },
    };
    view.rerender(
      <TableScreen table={liveTable} connectionStatus="connecting" />,
    );
    expect(screen.getByText('Connecting', { exact: true })).toHaveAttribute(
      'role',
      'status',
    );
    expect(
      screen.getByText('Connecting. The displayed table may be stale.'),
    ).not.toHaveAttribute('role');

    view.rerender(<TableScreen table={liveTable} connectionStatus="syncing" />);
    expect(screen.getByText('Syncing table')).toHaveAttribute('role', 'status');
    expect(
      screen.getByText('Connected. Waiting for a fresh table update.'),
    ).not.toHaveAttribute('role');

    view.rerender(
      <TableScreen table={liveTable} connectionStatus="connected" />,
    );
    expect(screen.getByText('Table live')).toHaveAttribute('role', 'status');
    expect(
      document.querySelector('.session-banner--connection'),
    ).not.toBeInTheDocument();

    view.rerender(
      <TableScreen table={liveTable} connectionStatus="disconnected" />,
    );
    expect(screen.getByText(/Disconnected .* stale table/)).toHaveAttribute(
      'role',
      'status',
    );
    expect(
      screen.getByText('Disconnected. The displayed table is stale.'),
    ).not.toHaveAttribute('role');
  });

  it('keeps stale live state visible through connecting and syncing', () => {
    const liveTable = {
      ...openDemoTable,
      mode: 'live' as const,
      chat: null,
      legalActions: null,
      roomPanel: { ...openDemoTable.roomPanel, canStartHand: false },
    };
    const view = render(
      <TableScreen table={liveTable} connectionStatus="connecting" />,
    );

    expect(
      screen.getByText('Connecting. The displayed table may be stale.'),
    ).toBeInTheDocument();
    expect(screen.getByText('The Lantern Room')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Start hand' })).toBeNull();
    expect(screen.queryByRole('region', { name: 'Chat' })).toBeNull();

    view.rerender(<TableScreen table={liveTable} connectionStatus="syncing" />);
    expect(
      screen.getByText('Connected. Waiting for a fresh table update.'),
    ).toBeInTheDocument();
    expect(screen.queryByText('Table live')).toBeNull();
  });

  it('submits server-described live fold and call intents without changing the table', () => {
    const snapshot = activeRoomSnapshot();
    const table = roomSnapshotToTableView(snapshot, 'guest_host');
    const onPokerAction = vi.fn(() => true);
    render(
      <TableScreen
        table={table}
        connectionStatus="connected"
        onPokerAction={onPokerAction}
      />,
    );
    const potBefore = screen.getByLabelText('Pot 250').textContent;

    fireEvent.click(screen.getByRole('button', { name: 'Fold' }));
    fireEvent.click(screen.getByRole('button', { name: 'Call 50' }));

    expect(onPokerAction).toHaveBeenNthCalledWith(1, {
      type: 'fold',
      contextKey: table.liveActions!.fold!.contextKey,
    });
    expect(onPokerAction).toHaveBeenNthCalledWith(2, {
      type: 'call',
      contextKey: table.liveActions!.middle!.contextKey,
    });
    expect(screen.getByLabelText('Pot 250').textContent).toBe(potBefore);
    expect(screen.getAllByText('9,850')).not.toHaveLength(0);
  });

  it('uses a total-to range and resets its draft only for changed context', () => {
    const snapshot = activeRoomSnapshot();
    const onPokerAction = vi.fn(() => true);
    const view = render(
      <TableScreen
        table={roomSnapshotToTableView(snapshot, 'guest_host')}
        connectionStatus="connected"
        onPokerAction={onPokerAction}
      />,
    );
    const input = screen.getByRole('spinbutton', { name: 'Raise total' });
    fireEvent.change(input, { target: { value: '500' } });
    expect(input).toHaveValue(500);

    view.rerender(
      <TableScreen
        table={roomSnapshotToTableView(structuredClone(snapshot), 'guest_host')}
        connectionStatus="connected"
        onPokerAction={onPokerAction}
      />,
    );
    expect(screen.getByRole('spinbutton', { name: 'Raise total' })).toHaveValue(
      500,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Raise 500' }));
    expect(onPokerAction).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'raise_to', totalTo: 500 }),
    );

    const advanced = structuredClone(snapshot);
    advanced.active_hand!.action_sequence += 1;
    view.rerender(
      <TableScreen
        table={roomSnapshotToTableView(advanced, 'guest_host')}
        connectionStatus="connected"
        onPokerAction={onPokerAction}
      />,
    );
    expect(screen.getByRole('spinbutton', { name: 'Raise total' })).toHaveValue(
      200,
    );
  });

  it('renders short all-in as one discrete total without slider or presets', () => {
    const snapshot = activeRoomSnapshot();
    snapshot.active_hand!.legal_actions.raise_to = {
      minimum_full_to: 200,
      maximum_to: 150,
      short_all_in_to: 150,
    };
    const onPokerAction = vi.fn(() => true);
    render(
      <TableScreen
        table={roomSnapshotToTableView(snapshot, 'guest_host')}
        connectionStatus="connected"
        onPokerAction={onPokerAction}
      />,
    );

    expect(screen.queryByRole('slider')).toBeNull();
    expect(screen.queryByRole('button', { name: /Min/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Max/ })).toBeNull();
    expect(screen.getByRole('spinbutton', { name: 'Raise total' })).toHaveValue(
      150,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Raise all-in 150' }));
    expect(onPokerAction).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'raise_to', totalTo: 150 }),
    );
  });

  it('shows pending feedback and disables all live controls', () => {
    const snapshot = activeRoomSnapshot();
    const table = roomSnapshotToTableView(snapshot, 'guest_host', 'connected', {
      commandId: 'pending',
      type: 'call',
      handNumber: 1,
      expectedActionSequence: 2,
      actorGuestId: 'guest_host',
    });
    render(
      <TableScreen
        table={table}
        connectionStatus="connected"
        onPokerAction={() => true}
      />,
    );
    expect(
      screen.getByRole('region', { name: 'Table actions' }),
    ).toHaveAttribute('aria-busy', 'true');
    expect(screen.getByText('Submitting call…')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Fold' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Call 50' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Raise 200' })).toBeDisabled();
  });

  it('does not expose live actions to a non-actor or stale viewer', () => {
    const snapshot = activeRoomSnapshot();
    const nonActor = roomSnapshotToTableView(snapshot, 'guest_alice');
    const view = render(
      <TableScreen table={nonActor} connectionStatus="connected" />,
    );
    expect(screen.getByText('Waiting for Mara')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Fold' })).toBeNull();

    view.rerender(
      <TableScreen
        table={roomSnapshotToTableView(snapshot, 'guest_host', 'disconnected')}
        connectionStatus="disconnected"
      />,
    );
    expect(screen.getByText('Disconnected')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Fold' })).toBeNull();
    expect(screen.getByLabelText('Pot 250')).toBeInTheDocument();
  });
});
