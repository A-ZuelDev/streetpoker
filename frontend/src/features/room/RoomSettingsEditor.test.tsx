import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { RoomSettingsEditor } from './RoomSettingsEditor';

const settings = {
  roomName: 'Friday Night',
  smallBlind: 50,
  bigBlind: 100,
  defaultStartingStack: 10_000,
  seatingApprovalRequired: true,
  standUpEnabled: false,
  standUpPenaltyPerRecipientChips: 100,
  actionTimeMs: 30_000,
  timebankTotalMs: 60_000,
  timebankRefillAmountMs: 60_000,
  timebankRefillEveryHands: 1,
  maxSeats: 6,
  passwordProtected: false,
};

describe('RoomSettingsEditor', () => {
  it('sends only changed fields from an explicit save', () => {
    const onSave = vi.fn(() => true);
    render(
      <RoomSettingsEditor
        settings={settings}
        roomCode="ABCDEFGH"
        handInProgress={false}
        disabled={false}
        onSave={onSave}
      />,
    );
    fireEvent.change(screen.getByRole('textbox', { name: 'Room name' }), {
      target: { value: 'Saturday Night' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    expect(onSave).toHaveBeenCalledWith({ room_name: 'Saturday Night' });
  });

  it('sends validated timer and additive refill settings in milliseconds', () => {
    const onSave = vi.fn(() => true);
    render(
      <RoomSettingsEditor
        settings={settings}
        roomCode="ABCDEFGH"
        handInProgress={false}
        disabled={false}
        onSave={onSave}
      />,
    );
    fireEvent.change(
      screen.getByRole('spinbutton', { name: 'Decision time (seconds)' }),
      { target: { value: '45' } },
    );
    fireEvent.change(
      screen.getByRole('spinbutton', { name: 'Time-bank total (seconds)' }),
      { target: { value: '90' } },
    );
    fireEvent.change(
      screen.getByRole('spinbutton', { name: 'Refill amount (seconds)' }),
      { target: { value: '15' } },
    );
    fireEvent.change(
      screen.getByRole('spinbutton', { name: 'Refill every (hands)' }),
      { target: { value: '3' } },
    );
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    expect(onSave).toHaveBeenCalledWith({
      action_time_ms: 45_000,
      timebank_total_ms: 90_000,
      timebank_refill_amount_ms: 15_000,
      timebank_refill_every_hands: 3,
    });
  });

  it('reconciles a successful no-op-normalized draft after pending clears', () => {
    const onSave = vi.fn(() => true);
    const rendered = render(
      <RoomSettingsEditor
        settings={settings}
        roomCode="ABCDEFGH"
        handInProgress={false}
        disabled={false}
        onSave={onSave}
      />,
    );
    fireEvent.change(screen.getByRole('textbox', { name: 'Room name' }), {
      target: { value: ' Friday   Night ' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    expect(onSave).toHaveBeenCalledWith({ room_name: ' Friday   Night ' });

    rendered.rerender(
      <RoomSettingsEditor
        settings={settings}
        roomCode="ABCDEFGH"
        handInProgress={false}
        disabled
        onSave={onSave}
      />,
    );
    rendered.rerender(
      <RoomSettingsEditor
        settings={settings}
        roomCode="ABCDEFGH"
        handInProgress={false}
        disabled={false}
        onSave={onSave}
      />,
    );
    expect(screen.getByRole('textbox', { name: 'Room name' })).toHaveValue(
      'Friday Night',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    expect(onSave).toHaveBeenCalledTimes(1);
  });

  it('defers Unicode room-name length rules to the authoritative server', () => {
    const onSave = vi.fn(() => true);
    render(
      <RoomSettingsEditor
        settings={settings}
        roomCode="ABCDEFGH"
        handInProgress={false}
        disabled={false}
        onSave={onSave}
      />,
    );
    const unicodeName = '🃏'.repeat(40);
    fireEvent.change(screen.getByRole('textbox', { name: 'Room name' }), {
      target: { value: unicodeName },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    expect(onSave).toHaveBeenCalledWith({ room_name: unicodeName });
  });

  it('clears a password immediately after the send attempt', () => {
    const onSave = vi.fn(() => true);
    render(
      <RoomSettingsEditor
        settings={settings}
        roomCode="ABCDEFGH"
        handInProgress={false}
        disabled={false}
        onSave={onSave}
      />,
    );
    fireEvent.change(screen.getByRole('combobox', { name: 'Password' }), {
      target: { value: 'set' },
    });
    const password = screen.getByLabelText('New password');
    fireEvent.change(password, { target: { value: 'private-password' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    expect(onSave).toHaveBeenCalledWith({ password: 'private-password' });
    expect(screen.queryByDisplayValue('private-password')).toBeNull();
    expect(document.body.textContent).not.toContain('private-password');
  });

  it('uses null to remove a password', () => {
    const onSave = vi.fn(() => true);
    render(
      <RoomSettingsEditor
        settings={{ ...settings, passwordProtected: true }}
        roomCode="ABCDEFGH"
        handInProgress={false}
        disabled={false}
        onSave={onSave}
      />,
    );
    fireEvent.change(screen.getByRole('combobox', { name: 'Password' }), {
      target: { value: 'remove' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    expect(onSave).toHaveBeenCalledWith({ password: null });
  });

  it('keeps safe settings editable but locks blind fields during a hand', () => {
    render(
      <RoomSettingsEditor
        settings={settings}
        roomCode="ABCDEFGH"
        handInProgress
        disabled={false}
        onSave={() => true}
      />,
    );
    expect(screen.getByRole('textbox', { name: 'Room name' })).toBeEnabled();
    expect(
      screen.getByRole('spinbutton', { name: 'Small blind' }),
    ).toBeDisabled();
    expect(
      screen.getByRole('spinbutton', { name: 'Big blind' }),
    ).toBeDisabled();
    expect(
      screen.getByRole('spinbutton', { name: 'Starting stack' }),
    ).toBeDisabled();
    expect(
      screen.getByRole('spinbutton', { name: 'Decision time (seconds)' }),
    ).toBeDisabled();
    expect(
      screen.getByRole('spinbutton', { name: 'Time-bank total (seconds)' }),
    ).toBeDisabled();
    expect(
      screen.getByRole('checkbox', { name: 'Enable Stand-Up side game' }),
    ).toBeEnabled();
    expect(
      screen.getByRole('spinbutton', { name: 'Penalty per player' }),
    ).toBeEnabled();
  });

  it('sends Stand-Up enablement and penalty through the settings patch', () => {
    const onSave = vi.fn(() => true);
    render(
      <RoomSettingsEditor
        settings={settings}
        roomCode="ABCDEFGH"
        handInProgress
        standUpActive
        disabled={false}
        onSave={onSave}
      />,
    );
    fireEvent.click(
      screen.getByRole('checkbox', { name: 'Enable Stand-Up side game' }),
    );
    fireEvent.change(
      screen.getByRole('spinbutton', { name: 'Penalty per player' }),
      { target: { value: '250' } },
    );
    expect(
      screen.getByText(/Turning Stand-Up off cancels/),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    expect(onSave).toHaveBeenCalledWith({
      stand_up_enabled: true,
      stand_up_penalty_per_recipient_chips: 250,
    });
  });

  it('rejects an unsafe Stand-Up penalty locally', () => {
    const onSave = vi.fn(() => true);
    render(
      <RoomSettingsEditor
        settings={settings}
        roomCode="ABCDEFGH"
        handInProgress={false}
        disabled={false}
        onSave={onSave}
      />,
    );
    fireEvent.change(
      screen.getByRole('spinbutton', { name: 'Penalty per player' }),
      { target: { value: '1801439850948200' } },
    );
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent(
      'safe positive whole number',
    );
  });

  it('ignores an invalid locked numeric draft for a legal mid-hand update', () => {
    const onSave = vi.fn(() => true);
    const rendered = render(
      <RoomSettingsEditor
        settings={settings}
        roomCode="ABCDEFGH"
        handInProgress={false}
        disabled={false}
        onSave={onSave}
      />,
    );
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Small blind' }), {
      target: { value: '' },
    });
    rendered.rerender(
      <RoomSettingsEditor
        settings={settings}
        roomCode="ABCDEFGH"
        handInProgress
        disabled={false}
        onSave={onSave}
      />,
    );
    fireEvent.change(screen.getByRole('textbox', { name: 'Room name' }), {
      target: { value: 'Saturday Night' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    expect(onSave).toHaveBeenCalledWith({ room_name: 'Saturday Night' });
  });

  it('rejects unsafe or inconsistent number drafts locally', () => {
    const onSave = vi.fn(() => true);
    render(
      <RoomSettingsEditor
        settings={settings}
        roomCode="ABCDEFGH"
        handInProgress={false}
        disabled={false}
        onSave={onSave}
      />,
    );
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Small blind' }), {
      target: { value: '9007199254740992' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent(
      'positive whole numbers',
    );
  });

  it('separates a disabled editor from actual settings-saving feedback', () => {
    const rendered = render(
      <RoomSettingsEditor
        settings={settings}
        roomCode="ABCDEFGH"
        handInProgress={false}
        disabled
        savingSettings={false}
        onSave={() => true}
      />,
    );
    expect(
      screen.getByRole('button', { name: 'Save settings' }),
    ).toBeDisabled();
    expect(screen.queryByText('Saving…')).toBeNull();
    expect(rendered.container.querySelector('.room-settings')).toHaveAttribute(
      'aria-busy',
      'false',
    );

    rendered.rerender(
      <RoomSettingsEditor
        settings={settings}
        roomCode="ABCDEFGH"
        handInProgress={false}
        disabled
        savingSettings
        onSave={() => true}
      />,
    );
    expect(screen.getByRole('button', { name: 'Saving…' })).toBeDisabled();
    expect(rendered.container.querySelector('.room-settings')).toHaveAttribute(
      'aria-busy',
      'true',
    );
  });
});
