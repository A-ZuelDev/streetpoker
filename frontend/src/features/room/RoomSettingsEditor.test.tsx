import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { RoomSettingsEditor } from './RoomSettingsEditor';

const settings = {
  roomName: 'Friday Night',
  smallBlind: 50,
  bigBlind: 100,
  defaultStartingStack: 10_000,
  seatingApprovalRequired: true,
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
