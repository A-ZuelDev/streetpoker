import { useState, type FormEvent } from 'react';

import type {
  CreateRoomInput,
  JoinRoomInput,
} from '../../realtime/useRoomSession';
import type {
  SessionError,
  SessionErrorField,
} from '../../realtime/sessionError';
import type { RoomExitState } from '../../realtime/realtimeStore';

interface RoomEntryProps {
  busy: boolean;
  statusText: string | null;
  error: SessionError | null;
  persistenceWarning: boolean;
  canReconnect: boolean;
  onCreate(input: CreateRoomInput): Promise<void>;
  onJoin(input: JoinRoomInput): void;
  onReconnect(): void;
  roomExit?: RoomExitState | null;
}

function positiveSafeInteger(value: string): number | null {
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null;
}

export function RoomEntry({
  busy,
  statusText,
  error,
  persistenceWarning,
  canReconnect,
  onCreate,
  onJoin,
  onReconnect,
  roomExit = null,
}: RoomEntryProps) {
  const [activeForm, setActiveForm] = useState<'create' | 'join' | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);
  const [createNickname, setCreateNickname] = useState('');
  const [roomName, setRoomName] = useState('');
  const [smallBlind, setSmallBlind] = useState('50');
  const [bigBlind, setBigBlind] = useState('100');
  const [startingStack, setStartingStack] = useState('10000');
  const [seatingApproval, setSeatingApproval] = useState(true);
  const [createPassword, setCreatePassword] = useState('');
  const [joinRoomCode, setJoinRoomCode] = useState('');
  const [joinNickname, setJoinNickname] = useState('');
  const [joinPassword, setJoinPassword] = useState('');

  const fieldError = (form: 'create' | 'join', field: SessionErrorField) =>
    activeForm === form && error?.field === field ? error.message : null;
  const renderFieldError = (
    form: 'create' | 'join',
    field: SessionErrorField,
    id: string,
  ) => {
    const message = fieldError(form, field);
    return message === null ? null : (
      <small id={id} role="alert">
        {message}
      </small>
    );
  };
  const generalError =
    error !== null && error.field === null ? error.message : localError;
  const exitedRoomName = roomExit?.roomName ?? 'the room';
  const roomExitMessage =
    roomExit?.kind === 'left'
      ? `You left ${exitedRoomName}.`
      : roomExit?.kind === 'kicked'
        ? `You were removed from ${exitedRoomName}.`
        : roomExit?.kind === 'closed'
          ? `${roomExit?.roomName ?? 'This room'} was closed.`
          : null;

  const submitCreate = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setActiveForm('create');
    setLocalError(null);
    const nickname = createNickname.trim();
    const name = roomName.trim();
    const small = positiveSafeInteger(smallBlind);
    const big = positiveSafeInteger(bigBlind);
    const stack = positiveSafeInteger(startingStack);
    if (nickname.length === 0 || name.length === 0) {
      setLocalError('Enter a nickname and room name.');
      return;
    }
    if (small === null || big === null || stack === null) {
      setLocalError(
        'Blinds and starting stack must be positive whole numbers.',
      );
      return;
    }
    if (big <= small || stack < big) {
      setLocalError(
        'The big blind must exceed the small blind, and the stack must cover it.',
      );
      return;
    }
    void onCreate({
      nickname,
      roomName: name,
      smallBlind: small,
      bigBlind: big,
      startingStack: stack,
      seatingApprovalRequired: seatingApproval,
      ...(createPassword === '' ? {} : { password: createPassword }),
    });
  };

  const submitJoin = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setActiveForm('join');
    setLocalError(null);
    const nickname = joinNickname.trim();
    if (nickname.length === 0) {
      setLocalError('Enter a nickname.');
      return;
    }
    if (
      !/^[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{8}$/.test(
        joinRoomCode.trim().toUpperCase(),
      )
    ) {
      setLocalError('Enter a valid eight-character room code.');
      return;
    }
    onJoin({
      roomCode: joinRoomCode,
      nickname,
      ...(joinPassword === '' ? {} : { password: joinPassword }),
    });
  };

  return (
    <main className="session-entry">
      <header className="session-entry__header">
        <span className="session-entry__eyebrow">
          Play-money private tables
        </span>
        <h1>StreetPoker</h1>
        <p>Create a room for friends or join with an eight-character code.</p>
      </header>

      {generalError !== null ? (
        <p className="session-entry__alert" role="alert">
          {generalError}
        </p>
      ) : null}
      {roomExitMessage !== null ? (
        <p className="session-entry__status" role="status">
          {roomExitMessage}
        </p>
      ) : null}
      {statusText !== null ? (
        <p className="session-entry__status" role="status">
          {statusText}
        </p>
      ) : null}
      {canReconnect && !busy ? (
        <button
          className="session-entry__reconnect"
          type="button"
          onClick={onReconnect}
        >
          Reconnect
        </button>
      ) : null}
      {persistenceWarning ? (
        <p className="session-entry__warning" role="status">
          Your browser session will not persist after this page closes.
        </p>
      ) : null}

      <div className="session-entry__forms">
        <form
          className="session-card"
          aria-busy={busy && activeForm === 'create'}
          onSubmit={submitCreate}
        >
          <div>
            <span className="session-card__step">New table</span>
            <h2>Create a room</h2>
          </div>
          <label>
            Nickname
            <input
              name="create-nickname"
              value={createNickname}
              maxLength={128}
              autoComplete="nickname"
              required
              aria-invalid={fieldError('create', 'nickname') !== null}
              aria-describedby={
                fieldError('create', 'nickname') === null
                  ? undefined
                  : 'create-nickname-error'
              }
              onChange={(event) => setCreateNickname(event.currentTarget.value)}
            />
            {renderFieldError('create', 'nickname', 'create-nickname-error')}
          </label>
          <label>
            Room name
            <input
              name="room-name"
              value={roomName}
              maxLength={512}
              required
              aria-invalid={fieldError('create', 'roomName') !== null}
              aria-describedby={
                fieldError('create', 'roomName') === null
                  ? undefined
                  : 'create-room-name-error'
              }
              onChange={(event) => setRoomName(event.currentTarget.value)}
            />
            {renderFieldError('create', 'roomName', 'create-room-name-error')}
          </label>
          <div className="session-card__number-grid">
            <label>
              Small blind
              <input
                name="small-blind"
                type="number"
                min="1"
                step="1"
                value={smallBlind}
                aria-invalid={fieldError('create', 'settings') !== null}
                aria-describedby={
                  fieldError('create', 'settings') === null
                    ? undefined
                    : 'create-settings-error'
                }
                onChange={(event) => setSmallBlind(event.currentTarget.value)}
              />
            </label>
            <label>
              Big blind
              <input
                name="big-blind"
                type="number"
                min="1"
                step="1"
                value={bigBlind}
                aria-invalid={fieldError('create', 'settings') !== null}
                aria-describedby={
                  fieldError('create', 'settings') === null
                    ? undefined
                    : 'create-settings-error'
                }
                onChange={(event) => setBigBlind(event.currentTarget.value)}
              />
            </label>
          </div>
          <label>
            Starting stack
            <input
              name="starting-stack"
              type="number"
              min="1"
              step="1"
              value={startingStack}
              aria-invalid={fieldError('create', 'settings') !== null}
              aria-describedby={
                fieldError('create', 'settings') === null
                  ? undefined
                  : 'create-settings-error'
              }
              onChange={(event) => setStartingStack(event.currentTarget.value)}
            />
            {renderFieldError('create', 'settings', 'create-settings-error')}
          </label>
          <label>
            Optional password
            <input
              name="create-password"
              type="password"
              value={createPassword}
              maxLength={128}
              autoComplete="new-password"
              aria-invalid={fieldError('create', 'password') !== null}
              aria-describedby={
                fieldError('create', 'password') === null
                  ? undefined
                  : 'create-password-error'
              }
              onChange={(event) => setCreatePassword(event.currentTarget.value)}
            />
            {renderFieldError('create', 'password', 'create-password-error')}
          </label>
          <label className="session-card__check">
            <input
              name="seating-approval"
              type="checkbox"
              checked={seatingApproval}
              onChange={(event) =>
                setSeatingApproval(event.currentTarget.checked)
              }
            />
            Require host approval for seats
          </label>
          <button type="submit" disabled={busy}>
            {busy && activeForm === 'create' ? 'Creating…' : 'Create room'}
          </button>
        </form>

        <form
          className="session-card"
          aria-busy={busy && activeForm === 'join'}
          onSubmit={submitJoin}
        >
          <div>
            <span className="session-card__step">Have a code?</span>
            <h2>Join a room</h2>
          </div>
          <label>
            Room code
            <input
              name="join-room-code"
              value={joinRoomCode}
              maxLength={10}
              autoCapitalize="characters"
              autoComplete="off"
              spellCheck={false}
              required
              aria-invalid={fieldError('join', 'roomCode') !== null}
              aria-describedby={
                fieldError('join', 'roomCode') === null
                  ? undefined
                  : 'join-room-code-error'
              }
              onChange={(event) => setJoinRoomCode(event.currentTarget.value)}
            />
            {renderFieldError('join', 'roomCode', 'join-room-code-error')}
          </label>
          <label>
            Nickname
            <input
              name="join-nickname"
              value={joinNickname}
              maxLength={128}
              autoComplete="nickname"
              required
              aria-invalid={fieldError('join', 'nickname') !== null}
              aria-describedby={
                fieldError('join', 'nickname') === null
                  ? undefined
                  : 'join-nickname-error'
              }
              onChange={(event) => setJoinNickname(event.currentTarget.value)}
            />
            {renderFieldError('join', 'nickname', 'join-nickname-error')}
          </label>
          <label>
            Password if required
            <input
              name="join-password"
              type="password"
              value={joinPassword}
              maxLength={128}
              autoComplete="current-password"
              aria-invalid={fieldError('join', 'password') !== null}
              aria-describedby={
                fieldError('join', 'password') === null
                  ? undefined
                  : 'join-password-error'
              }
              onChange={(event) => setJoinPassword(event.currentTarget.value)}
            />
            {renderFieldError('join', 'password', 'join-password-error')}
          </label>
          <button type="submit" disabled={busy}>
            {busy && activeForm === 'join' ? 'Connecting…' : 'Join room'}
          </button>
        </form>
      </div>
    </main>
  );
}
