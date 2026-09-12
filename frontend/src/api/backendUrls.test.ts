import { describe, expect, it } from 'vitest';

import { canonicalizeRoomCode, createBackendUrls } from './backendUrls';

describe('createBackendUrls', () => {
  it.each([
    [
      'http://127.0.0.1:8000',
      'http://127.0.0.1:8000/rooms',
      'http://127.0.0.1:8000/health',
      'ws://127.0.0.1:8000/ws/rooms/ABCDEFGH',
    ],
    [
      'https://example.test/api/',
      'https://example.test/api/rooms',
      'https://example.test/api/health',
      'wss://example.test/api/ws/rooms/ABCDEFGH',
    ],
    [
      'https://example.test/api///',
      'https://example.test/api/rooms',
      'https://example.test/api/health',
      'wss://example.test/api/ws/rooms/ABCDEFGH',
    ],
  ])('builds HTTP and socket URLs from %s', (raw, rooms, health, socket) => {
    const urls = createBackendUrls(raw);

    expect(urls.roomsUrl).toBe(rooms);
    expect(urls.healthUrl).toBe(health);
    expect(urls.roomWebSocketUrl(' abcdefgh ')).toBe(socket);
  });

  it.each([
    'ftp://example.test',
    'ws://example.test',
    'https://user@example.test',
    'https://user:password@example.test',
    'https://example.test?region=test',
    'https://example.test?',
    'https://example.test#fragment',
    'https://example.test#',
    'not a url',
  ])('rejects unsafe base URL %s', (value) => {
    expect(() => createBackendUrls(value)).toThrow(
      'The backend URL configuration is invalid.',
    );
  });
});

describe('canonicalizeRoomCode', () => {
  it('normalizes human-entered codes', () => {
    expect(canonicalizeRoomCode(' abcd2345 ')).toBe('ABCD2345');
  });

  it.each(['short', 'ABCDEFGI', 'ABCDEFG0', 'ABCDEFG1', 'ABCDEFGHH'])(
    'rejects invalid code %s',
    (value) => {
      expect(() => canonicalizeRoomCode(value)).toThrow(
        'The room code is invalid.',
      );
    },
  );
});
