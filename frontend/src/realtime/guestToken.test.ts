import { beforeEach, describe, expect, it, vi } from 'vitest';

function fixedCrypto(fill: number, lengths: number[]): Crypto {
  return {
    getRandomValues(array: Uint8Array) {
      lengths.push(array.byteLength);
      array.fill(fill);
      return array;
    },
  } as unknown as Crypto;
}

function memoryStorage(initial?: string): Storage {
  const values = new Map<string, string>();
  if (initial !== undefined) {
    values.set('streetpoker.guest-token.v1', initial);
  }
  return {
    get length() {
      return values.size;
    },
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    key: (index) => [...values.keys()][index] ?? null,
    removeItem: (key) => values.delete(key),
    setItem: (key, value) => values.set(key, value),
  };
}

beforeEach(() => {
  vi.resetModules();
});

describe('getOrCreateGuestToken', () => {
  it('generates exactly 32 bytes and persists a 43-character token', async () => {
    const lengths: number[] = [];
    const storage = memoryStorage();
    const { getOrCreateGuestToken, GUEST_TOKEN_STORAGE_KEY } =
      await import('./guestToken');

    const result = getOrCreateGuestToken({
      storage,
      crypto: fixedCrypto(7, lengths),
    });

    expect(lengths).toEqual([32]);
    expect(result.token).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(storage.getItem(GUEST_TOKEN_STORAGE_KEY)).toBe(result.token);
    expect(result.persistent).toBe(true);
  });

  it('reuses a canonical stored token without generating entropy', async () => {
    const canonical = btoa(String.fromCharCode(...new Uint8Array(32).fill(3)))
      .replace(/\+/g, '-')
      .replace(/\//g, '_')
      .replace(/=+$/, '');
    const lengths: number[] = [];
    const { getOrCreateGuestToken } = await import('./guestToken');

    const result = getOrCreateGuestToken({
      storage: memoryStorage(canonical),
      crypto: fixedCrypto(8, lengths),
    });

    expect(result).toEqual({ token: canonical, persistent: true });
    expect(lengths).toEqual([]);
  });

  it.each(['bad', 'A'.repeat(42), `${'A'.repeat(42)}B`])(
    'replaces invalid or noncanonical stored value',
    async (stored) => {
      const storage = memoryStorage(stored);
      const { getOrCreateGuestToken, GUEST_TOKEN_STORAGE_KEY } =
        await import('./guestToken');

      const result = getOrCreateGuestToken({
        storage,
        crypto: fixedCrypto(9, []),
      });

      expect(result.token).not.toBe(stored);
      expect(storage.getItem(GUEST_TOKEN_STORAGE_KEY)).toBe(result.token);
    },
  );

  it('keeps one page-lifetime fallback when storage throws', async () => {
    const lengths: number[] = [];
    const storage = {
      getItem: () => {
        throw new Error('private storage detail');
      },
      setItem: () => {
        throw new Error('private storage detail');
      },
    } as unknown as Storage;
    const { getOrCreateGuestToken } = await import('./guestToken');

    const first = getOrCreateGuestToken({
      storage,
      crypto: fixedCrypto(10, lengths),
    });
    const second = getOrCreateGuestToken({
      storage,
      crypto: fixedCrypto(11, lengths),
    });

    expect(first).toEqual(second);
    expect(first.persistent).toBe(false);
    expect(lengths).toEqual([32]);
  });
});
