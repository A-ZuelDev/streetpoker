import { afterEach, describe, expect, it, vi } from 'vitest';

import { SessionError } from '../realtime/sessionError';
import { createRoom, type CreateRoomRequest } from './rooms';

const request: CreateRoomRequest = {
  guest_token: 'A'.repeat(43),
  nickname: 'Mara',
  settings: {
    room_name: 'Friday Night',
    small_blind: 50,
    big_blind: 100,
    default_starting_stack: 10_000,
    seating_approval_required: true,
  },
  password: 'private-password',
};

function response(options: {
  ok: boolean;
  status: number;
  body?: unknown;
  jsonError?: boolean;
}): Response {
  return {
    ok: options.ok,
    status: options.status,
    json: options.jsonError
      ? vi.fn().mockRejectedValue(new Error('raw response'))
      : vi.fn().mockResolvedValue(options.body),
  } as unknown as Response;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('createRoom', () => {
  it('posts the exact request once and strictly parses success', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        response({ ok: true, status: 201, body: { room_code: 'ABCDEFGH' } }),
      );
    vi.stubGlobal('fetch', fetchMock);

    await expect(createRoom(request)).resolves.toEqual({
      room_code: 'ABCDEFGH',
    });
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('http://127.0.0.1:8000/rooms');
    expect(init).toMatchObject({
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      cache: 'no-store',
      signal: null,
    });
    expect(JSON.parse(String(init.body))).toEqual(request);
  });

  it.each([
    [400, 'invalid_guest_token', 'The browser session could not be accepted.'],
    [413, 'request_too_large', 'The room request is too large.'],
    [422, 'invalid_nickname', 'Enter a valid nickname.'],
    [422, 'invalid_room_name', 'Enter a valid room name.'],
    [422, 'invalid_settings', 'Check the room blind and stack settings.'],
    [422, 'invalid_room_password', 'Enter a valid room password.'],
    [500, 'internal_error', 'The server could not create the room.'],
    [
      503,
      'room_creation_unavailable',
      'A room could not be created right now.',
    ],
  ])('maps HTTP %s/%s to safe text', async (status, code, message) => {
    const fetchMock = vi.fn().mockResolvedValue(
      response({
        ok: false,
        status,
        body: { error: { code, message: 'private backend detail' } },
      }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const error = await createRoom(request).catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(SessionError);
    expect(error).toMatchObject({ code, message, status });
    expect(String(error)).not.toContain('private-password');
    expect(String(error)).not.toContain('private backend detail');
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it.each([
    { room_code: 'abcdefgh' },
    { room_code: 'ABCDEFGH', extra: true },
    {},
  ])('rejects malformed success %#', async (body) => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(response({ ok: true, status: 201, body })),
    );

    await expect(createRoom(request)).rejects.toMatchObject({
      code: 'invalid_room_response',
      ambiguous: true,
    });
  });

  it('returns a safe ambiguous error for a network failure without retrying', async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValue(new Error('private network detail'));
    vi.stubGlobal('fetch', fetchMock);

    await expect(createRoom(request)).rejects.toMatchObject({
      code: 'room_creation_network_error',
      message: 'The room creation result could not be confirmed.',
      ambiguous: true,
    });
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it('uses a generic safe error when an error body is malformed', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          response({ ok: false, status: 500, jsonError: true }),
        ),
    );

    await expect(createRoom(request)).rejects.toMatchObject({
      code: 'http_500',
      message: 'The server could not create the room.',
    });
  });
});
