import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { activeDemoTable, openDemoTable } from './demoTable.fixture';
import { TableScreen } from './TableScreen';

describe('TableScreen', () => {
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
    expect(screen.getByText('You')).toBeInTheDocument();
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

    expect(screen.getByRole('button', { name: 'Fold' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Call 350' })).toBeEnabled();
    expect(screen.getByLabelText('Bet or raise amount')).toHaveValue(2_400);
    expect(screen.getByLabelText('Bet or raise amount slider')).toHaveValue(
      '2400',
    );
    expect(screen.getByRole('button', { name: 'Raise 2,400' })).toBeEnabled();
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
      screen.getByRole('button', { name: 'Collapse Chat section' }),
    );
    const chat = screen.getByRole('region', { name: 'Chat' });
    expect(within(chat).queryByText('nice hand')).toBeNull();
    expect(within(chat).queryByRole('textbox')).toBeNull();
    fireEvent.click(
      screen.getByRole('button', { name: 'Expand Chat section' }),
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

  it('renders accessible demo chat and appends a local-only message', () => {
    render(<TableScreen table={activeDemoTable} backendStatus="online" />);

    const chat = screen.getByRole('region', { name: 'Chat' });
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
    expect(screen.getByRole('button', { name: 'Raise 3,200' })).toBeEnabled();

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
    fireEvent.click(screen.getByRole('button', { name: 'Raise 8,100' }));
    expect(amount).toHaveValue(8_100);
    expect(slider).toHaveValue('8100');
    expect(screen.getByRole('button', { name: 'Raise 8,100' })).toBeEnabled();
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
    expect(screen.getByRole('button', { name: 'Raise 3,200' })).toBeEnabled();

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
    expect(screen.getByRole('button', { name: 'Raise 4,500' })).toBeEnabled();
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
    expect(screen.getByRole('button', { name: 'Start hand' })).toBeEnabled();
    expect(screen.getByText('Backend offline')).toBeInTheDocument();
  });
});
