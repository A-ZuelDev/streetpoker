import { useEffect, useRef, useState, type FormEvent } from 'react';

import {
  roomNameTransportLimit,
  type RoomSettingsPatch,
} from '../../realtime/roomCommands';
import type { RoomSettingsView } from '../table/table.types';

interface RoomSettingsEditorProps {
  settings: RoomSettingsView;
  roomCode: string;
  handInProgress: boolean;
  disabled: boolean;
  savingSettings?: boolean;
  onSave(patch: RoomSettingsPatch): boolean;
}

function positiveSafeInteger(value: string): number | null {
  if (!/^[1-9]\d*$/.test(value)) {
    return null;
  }
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) ? parsed : null;
}

export function RoomSettingsEditor({
  settings,
  roomCode,
  handInProgress,
  disabled,
  savingSettings = false,
  onSave,
}: RoomSettingsEditorProps) {
  const [roomName, setRoomName] = useState(settings.roomName);
  const [smallBlind, setSmallBlind] = useState(String(settings.smallBlind));
  const [bigBlind, setBigBlind] = useState(String(settings.bigBlind));
  const [startingStack, setStartingStack] = useState(
    String(settings.defaultStartingStack),
  );
  const [approvalRequired, setApprovalRequired] = useState(
    settings.seatingApprovalRequired,
  );
  const [passwordAction, setPasswordAction] = useState<
    'unchanged' | 'set' | 'remove'
  >('unchanged');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const wasDisabled = useRef(disabled);
  const submittedSettings = useRef(false);

  useEffect(() => {
    if (submittedSettings.current && wasDisabled.current && !disabled) {
      setRoomName(settings.roomName);
      setSmallBlind(String(settings.smallBlind));
      setBigBlind(String(settings.bigBlind));
      setStartingStack(String(settings.defaultStartingStack));
      setApprovalRequired(settings.seatingApprovalRequired);
      setPasswordAction('unchanged');
      setPassword('');
      setError(null);
      submittedSettings.current = false;
    }
    wasDisabled.current = disabled;
  }, [disabled, settings]);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    const passwordDraft = password;
    setPassword('');

    const name = roomName;
    const small = handInProgress
      ? settings.smallBlind
      : positiveSafeInteger(smallBlind);
    const big = handInProgress
      ? settings.bigBlind
      : positiveSafeInteger(bigBlind);
    const stack = handInProgress
      ? settings.defaultStartingStack
      : positiveSafeInteger(startingStack);
    if (name.trim().length === 0 || name.length > roomNameTransportLimit) {
      setError('Enter a room name within the transport limit.');
      return;
    }
    if (small === null || big === null || stack === null) {
      setError('Blinds and starting stack must be positive whole numbers.');
      return;
    }
    if (big <= small || stack < big) {
      setError(
        'The big blind must exceed the small blind, and the stack must cover it.',
      );
      return;
    }
    if (
      passwordAction === 'set' &&
      (passwordDraft.length === 0 ||
        passwordDraft.length > 128 ||
        new TextEncoder().encode(passwordDraft).length > 256)
    ) {
      setError('Enter a nonempty password within the allowed length.');
      return;
    }

    const patch: RoomSettingsPatch = {};
    if (name !== settings.roomName) {
      patch.room_name = name;
    }
    if (!handInProgress && small !== settings.smallBlind) {
      patch.small_blind = small;
    }
    if (!handInProgress && big !== settings.bigBlind) {
      patch.big_blind = big;
    }
    if (!handInProgress && stack !== settings.defaultStartingStack) {
      patch.default_starting_stack = stack;
    }
    if (approvalRequired !== settings.seatingApprovalRequired) {
      patch.seating_approval_required = approvalRequired;
    }
    if (passwordAction === 'set') {
      patch.password = passwordDraft;
    } else if (passwordAction === 'remove') {
      patch.password = null;
    }
    if (Object.keys(patch).length === 0) {
      setError('Change at least one setting before saving.');
      return;
    }
    if (onSave(patch)) {
      submittedSettings.current = true;
      setPasswordAction('unchanged');
    }
  };

  return (
    <form
      className="room-settings"
      aria-label="Room settings"
      aria-busy={savingSettings}
      onSubmit={submit}
    >
      <label>
        Room name
        <input
          value={roomName}
          maxLength={roomNameTransportLimit}
          disabled={disabled}
          onChange={(event) => setRoomName(event.currentTarget.value)}
        />
      </label>
      <div className="room-settings__numbers">
        <label>
          Small blind
          <input
            type="number"
            min="1"
            step="1"
            value={smallBlind}
            disabled={disabled || handInProgress}
            onChange={(event) => setSmallBlind(event.currentTarget.value)}
          />
        </label>
        <label>
          Big blind
          <input
            type="number"
            min="1"
            step="1"
            value={bigBlind}
            disabled={disabled || handInProgress}
            onChange={(event) => setBigBlind(event.currentTarget.value)}
          />
        </label>
      </div>
      <label>
        Starting stack
        <input
          type="number"
          min="1"
          step="1"
          value={startingStack}
          disabled={disabled || handInProgress}
          onChange={(event) => setStartingStack(event.currentTarget.value)}
        />
      </label>
      {handInProgress ? (
        <small>
          Blind and starting-stack changes wait until between hands.
        </small>
      ) : null}
      <label className="room-settings__check">
        <input
          type="checkbox"
          checked={approvalRequired}
          disabled={disabled}
          onChange={(event) => setApprovalRequired(event.currentTarget.checked)}
        />
        Require host approval for seats
      </label>
      <label>
        Password
        <select
          value={passwordAction}
          disabled={disabled}
          onChange={(event) => {
            setPassword('');
            setPasswordAction(
              event.currentTarget.value as 'unchanged' | 'set' | 'remove',
            );
          }}
        >
          <option value="unchanged">Leave unchanged</option>
          <option value="set">
            {settings.passwordProtected ? 'Replace password' : 'Set password'}
          </option>
          <option value="remove" disabled={!settings.passwordProtected}>
            Remove password
          </option>
        </select>
      </label>
      {passwordAction === 'set' ? (
        <label>
          New password
          <input
            type="password"
            value={password}
            maxLength={128}
            autoComplete="new-password"
            disabled={disabled}
            onChange={(event) => setPassword(event.currentTarget.value)}
          />
        </label>
      ) : null}
      <dl className="room-settings__readonly">
        <div>
          <dt>Room code</dt>
          <dd>{roomCode}</dd>
        </div>
        <div>
          <dt>Seats</dt>
          <dd>{settings.maxSeats}</dd>
        </div>
        <div>
          <dt>Password</dt>
          <dd>{settings.passwordProtected ? 'Protected' : 'Not protected'}</dd>
        </div>
      </dl>
      {error === null ? null : (
        <p className="room-settings__error" role="alert">
          {error}
        </p>
      )}
      <button type="submit" disabled={disabled}>
        {savingSettings ? 'Saving…' : 'Save settings'}
      </button>
    </form>
  );
}
