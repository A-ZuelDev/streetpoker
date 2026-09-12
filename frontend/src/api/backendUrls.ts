const ROOM_CODE_PATTERN = /^[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{8}$/;

export interface BackendUrls {
  readonly baseUrl: string;
  readonly healthUrl: string;
  readonly roomsUrl: string;
  roomWebSocketUrl(roomCode: string): string;
}

export function canonicalizeRoomCode(value: string): string {
  const canonical = value.trim().toUpperCase();

  if (!ROOM_CODE_PATTERN.test(canonical)) {
    throw new Error('The room code is invalid.');
  }

  return canonical;
}

function appendPath(base: URL, path: string): URL {
  const result = new URL(base);
  const prefix = result.pathname === '/' ? '' : result.pathname;
  result.pathname = `${prefix}/${path}`;
  return result;
}

export function createBackendUrls(rawBaseUrl: string): BackendUrls {
  if (rawBaseUrl.includes('?') || rawBaseUrl.includes('#')) {
    throw new Error('The backend URL configuration is invalid.');
  }

  let base: URL;
  try {
    base = new URL(rawBaseUrl);
  } catch {
    throw new Error('The backend URL configuration is invalid.');
  }

  if (base.protocol !== 'http:' && base.protocol !== 'https:') {
    throw new Error('The backend URL configuration is invalid.');
  }
  if (base.username !== '' || base.password !== '') {
    throw new Error('The backend URL configuration is invalid.');
  }
  if (base.search !== '' || base.hash !== '') {
    throw new Error('The backend URL configuration is invalid.');
  }

  base.pathname = base.pathname.replace(/\/+$/, '') || '/';
  const baseUrl = base
    .toString()
    .replace(/\/$/, base.pathname === '/' ? '' : '');
  const roomsUrl = appendPath(base, 'rooms').toString();
  const healthUrl = appendPath(base, 'health').toString();

  return {
    baseUrl,
    healthUrl,
    roomsUrl,
    roomWebSocketUrl(roomCode: string) {
      const canonical = canonicalizeRoomCode(roomCode);
      const result = appendPath(
        base,
        `ws/rooms/${encodeURIComponent(canonical)}`,
      );
      result.protocol = base.protocol === 'https:' ? 'wss:' : 'ws:';
      return result.toString();
    },
  };
}
