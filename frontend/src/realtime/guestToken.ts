export const GUEST_TOKEN_STORAGE_KEY = 'streetpoker.guest-token.v1';

const TOKEN_PATTERN = /^[A-Za-z0-9_-]{43}$/;

let pageToken: string | null = null;
let pageTokenPersistent = false;

function encodeBase64Url(bytes: Uint8Array): string {
  let binary = '';
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary)
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');
}

function decodeBase64Url(value: string): Uint8Array | null {
  try {
    const base64 = value.replace(/-/g, '+').replace(/_/g, '/') + '=';
    const binary = atob(base64);
    return Uint8Array.from(binary, (character) => character.charCodeAt(0));
  } catch {
    return null;
  }
}

export function isCanonicalGuestToken(value: unknown): value is string {
  if (typeof value !== 'string' || !TOKEN_PATTERN.test(value)) {
    return false;
  }
  const decoded = decodeBase64Url(value);
  return (
    decoded !== null &&
    decoded.length === 32 &&
    encodeBase64Url(decoded) === value
  );
}

function generateGuestToken(cryptoSource: Crypto): string {
  const bytes = new Uint8Array(32);
  cryptoSource.getRandomValues(bytes);
  const token = encodeBase64Url(bytes);
  if (!isCanonicalGuestToken(token)) {
    throw new Error('The browser could not create a guest session.');
  }
  return token;
}

function browserStorage(): Storage | null {
  try {
    return globalThis.localStorage;
  } catch {
    return null;
  }
}

export interface GuestTokenResult {
  readonly token: string;
  readonly persistent: boolean;
}

export function getOrCreateGuestToken(options?: {
  storage?: Storage | null;
  crypto?: Crypto;
}): GuestTokenResult {
  if (pageToken !== null) {
    return { token: pageToken, persistent: pageTokenPersistent };
  }

  const storage =
    options?.storage === undefined ? browserStorage() : options.storage;
  try {
    const stored = storage?.getItem(GUEST_TOKEN_STORAGE_KEY);
    if (isCanonicalGuestToken(stored)) {
      pageToken = stored;
      pageTokenPersistent = true;
      return { token: stored, persistent: true };
    }
  } catch {
    // The page-lifetime fallback below preserves identity for this mounted app.
  }

  const cryptoSource = options?.crypto ?? globalThis.crypto;
  if (cryptoSource === undefined) {
    throw new Error('The browser could not create a guest session.');
  }
  pageToken = generateGuestToken(cryptoSource);

  try {
    storage?.setItem(GUEST_TOKEN_STORAGE_KEY, pageToken);
    pageTokenPersistent = storage !== null;
  } catch {
    pageTokenPersistent = false;
  }

  return { token: pageToken, persistent: pageTokenPersistent };
}
