import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { SessionAccounting, StackAdjustmentForm } from './StackAccounting';

const members = [
  {
    guestId: 'guest_host',
    nickname: 'Host',
    status: 'Seated',
    stack: 1_000,
    isHost: true,
  },
  {
    guestId: 'guest_alice',
    nickname: 'Alice',
    status: 'Seated',
    stack: 1_500,
    isHost: false,
  },
];

describe('StackAdjustmentForm', () => {
  it('shows a resulting-stack preview and submits explicit semantics', () => {
    const onRoomCommand = vi.fn(() => true);
    render(
      <StackAdjustmentForm
        members={members}
        disabled={false}
        onRoomCommand={onRoomCommand}
      />,
    );

    fireEvent.change(screen.getByLabelText('Player'), {
      target: { value: 'guest_alice' },
    });
    fireEvent.change(screen.getByLabelText('Type'), {
      target: { value: 'cash_out' },
    });
    fireEvent.change(screen.getByLabelText('Amount'), {
      target: { value: '500' },
    });
    fireEvent.change(screen.getByLabelText(/Reason/), {
      target: { value: 'Early cash-out' },
    });

    expect(screen.getByText('Resulting stack: 1,000')).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: 'Apply ledgered adjustment' }),
    );
    expect(onRoomCommand).toHaveBeenCalledWith({
      type: 'adjust_stack',
      targetGuestId: 'guest_alice',
      adjustmentType: 'cash_out',
      amount: 500,
      reason: 'Early cash-out',
    });
  });

  it('supports signed corrections and blocks a negative resulting stack', () => {
    render(<StackAdjustmentForm members={members} disabled={false} />);
    fireEvent.change(screen.getByLabelText('Type'), {
      target: { value: 'correction' },
    });
    fireEvent.change(screen.getByLabelText('Signed amount'), {
      target: { value: '-1001' },
    });
    expect(
      screen.getByRole('button', { name: 'Apply ledgered adjustment' }),
    ).toBeDisabled();
  });
});

describe('SessionAccounting', () => {
  it('renders current, external, poker-net, and immutable history semantics', () => {
    render(
      <SessionAccounting
        session={{
          ledgerSequence: 1,
          players: [
            {
              nickname: 'Alice',
              seatNumber: 2,
              currentStack: 1_400,
              startingStack: 1_000,
              externalAdded: 500,
              externalRemoved: 0,
              pokerNet: -100,
              handsPlayed: 2,
            },
          ],
          adjustments: [
            {
              sequence: 1,
              type: 'rebuy',
              targetNickname: 'Alice',
              targetSeatNumber: 2,
              delta: 500,
              resultingStack: 1_500,
              reason: 'Top-up',
            },
          ],
        }}
      />,
    );

    expect(screen.getByText('Poker net')).toBeInTheDocument();
    expect(screen.getByText('-100')).toBeInTheDocument();
    expect(screen.getByText(/#1/)).toBeInTheDocument();
    expect(screen.getByText(/Rebuy \/ top-up/)).toBeInTheDocument();
    expect(screen.getByText('Top-up')).toBeInTheDocument();
  });
});
