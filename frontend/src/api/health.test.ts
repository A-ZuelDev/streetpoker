import { describe, expect, it } from 'vitest';

import { buildHealthUrl } from './health';

describe('buildHealthUrl', () => {
  it.each([
    ['http://127.0.0.1:8000', 'http://127.0.0.1:8000/health'],
    ['http://127.0.0.1:8000/', 'http://127.0.0.1:8000/health'],
  ])('joins %s with the health path', (apiBaseUrl, expectedUrl) => {
    expect(buildHealthUrl(apiBaseUrl)).toBe(expectedUrl);
  });
});
