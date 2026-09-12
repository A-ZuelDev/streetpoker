export type SessionErrorSource =
  'http' | 'websocket' | 'network' | 'protocol' | 'configuration';

export type SessionErrorField =
  'nickname' | 'roomName' | 'settings' | 'password' | 'roomCode';

export class SessionError extends Error {
  readonly source: SessionErrorSource;
  readonly code: string;
  readonly status: number | null;
  readonly field: SessionErrorField | null;
  readonly ambiguous: boolean;

  constructor(options: {
    source: SessionErrorSource;
    code: string;
    message: string;
    status?: number | null;
    field?: SessionErrorField | null;
    ambiguous?: boolean;
  }) {
    super(options.message);
    this.name = 'SessionError';
    this.source = options.source;
    this.code = options.code;
    this.status = options.status ?? null;
    this.field = options.field ?? null;
    this.ambiguous = options.ambiguous ?? false;
  }
}

const CONNECTION_ERRORS: Record<
  string,
  { message: string; field?: SessionErrorField }
> = {
  wrong_room_password: {
    message: 'The room password was not accepted.',
    field: 'password',
  },
  invalid_room_password: {
    message: 'The room password is invalid.',
    field: 'password',
  },
  duplicate_nickname: {
    message: 'That nickname is already in use.',
    field: 'nickname',
  },
  invalid_nickname: {
    message: 'Enter a valid nickname.',
    field: 'nickname',
  },
  invalid_room_code: {
    message: 'Enter a valid room code.',
    field: 'roomCode',
  },
  room_not_found: {
    message: 'That room could not be found.',
    field: 'roomCode',
  },
  room_closed: { message: 'That room is closed.' },
  membership_required: {
    message: 'A nickname is required to join this room.',
    field: 'nickname',
  },
  handshake_timeout: { message: 'The room connection timed out.' },
  invalid_handshake: { message: 'The room connection was rejected.' },
  invalid_guest_token: {
    message: 'The browser session could not be accepted.',
  },
  internal_error: {
    message: 'The server could not establish the room connection.',
  },
};

export function connectionSessionError(code: string): SessionError {
  const known = CONNECTION_ERRORS[code];
  return new SessionError({
    source: 'websocket',
    code,
    message: known?.message ?? 'The room connection could not be established.',
    field: known?.field ?? null,
  });
}

export function networkSessionError(): SessionError {
  return new SessionError({
    source: 'network',
    code: 'network_error',
    message: 'The server could not be reached.',
    ambiguous: true,
  });
}

export function protocolSessionError(): SessionError {
  return new SessionError({
    source: 'protocol',
    code: 'invalid_server_message',
    message: 'The server sent an invalid room update.',
  });
}
