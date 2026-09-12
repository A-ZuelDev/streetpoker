import { z } from 'zod';

import { backendUrls } from '../env';
import { SessionError, type SessionErrorField } from '../realtime/sessionError';

const roomCodeSchema = z
  .string()
  .regex(/^[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{8}$/);
const createRoomResponseSchema = z.strictObject({
  room_code: roomCodeSchema,
});
const roomErrorResponseSchema = z.strictObject({
  error: z.strictObject({
    code: z.string(),
    message: z.string(),
  }),
});

export interface CreateRoomRequest {
  readonly guest_token: string;
  readonly nickname: string;
  readonly settings: {
    readonly room_name: string;
    readonly small_blind: number;
    readonly big_blind: number;
    readonly default_starting_stack: number;
    readonly seating_approval_required: boolean;
  };
  readonly password?: string;
}

export type CreateRoomResponse = z.infer<typeof createRoomResponseSchema>;

const HTTP_ERROR_DETAILS: Record<
  string,
  { message: string; field?: SessionErrorField }
> = {
  invalid_guest_token: {
    message: 'The browser session could not be accepted.',
  },
  request_too_large: { message: 'The room request is too large.' },
  invalid_nickname: { message: 'Enter a valid nickname.', field: 'nickname' },
  invalid_room_name: { message: 'Enter a valid room name.', field: 'roomName' },
  invalid_settings: {
    message: 'Check the room blind and stack settings.',
    field: 'settings',
  },
  invalid_room_password: {
    message: 'Enter a valid room password.',
    field: 'password',
  },
  validation_error: { message: 'Check the room details and try again.' },
  room_creation_unavailable: {
    message: 'A room could not be created right now.',
  },
  internal_error: { message: 'The server could not create the room.' },
};

async function httpSessionError(response: Response): Promise<SessionError> {
  let code = `http_${response.status}`;
  try {
    const parsed = roomErrorResponseSchema.safeParse(await response.json());
    if (parsed.success) {
      code = parsed.data.error.code;
    }
  } catch {
    // The response body is intentionally not exposed.
  }

  const known = HTTP_ERROR_DETAILS[code];
  const fallback =
    response.status === 413
      ? 'The room request is too large.'
      : response.status === 422
        ? 'Check the room details and try again.'
        : response.status === 503
          ? 'A room could not be created right now.'
          : 'The server could not create the room.';
  return new SessionError({
    source: 'http',
    code,
    message: known?.message ?? fallback,
    status: response.status,
    field: known?.field ?? null,
    ambiguous: response.status >= 500 && response.status !== 503,
  });
}

export async function createRoom(
  request: CreateRoomRequest,
  signal?: AbortSignal,
): Promise<CreateRoomResponse> {
  let response: Response;
  try {
    response = await fetch(backendUrls.roomsUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
      cache: 'no-store',
      signal: signal ?? null,
    });
  } catch {
    throw new SessionError({
      source: 'network',
      code: 'room_creation_network_error',
      message: 'The room creation result could not be confirmed.',
      ambiguous: true,
    });
  }

  if (!response.ok) {
    throw await httpSessionError(response);
  }

  try {
    return createRoomResponseSchema.parse(await response.json());
  } catch {
    throw new SessionError({
      source: 'protocol',
      code: 'invalid_room_response',
      message: 'The room creation result could not be confirmed.',
      status: response.status,
      ambiguous: true,
    });
  }
}
