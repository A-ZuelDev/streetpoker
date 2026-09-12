import { z } from 'zod';

import { backendUrls } from '../env';
import { createBackendUrls } from './backendUrls';

const healthResponseSchema = z.object({
  status: z.literal('ok'),
});

export type HealthResponse = z.infer<typeof healthResponseSchema>;

export function buildHealthUrl(apiBaseUrl: string): string {
  return createBackendUrls(apiBaseUrl).healthUrl;
}

export async function fetchHealth(
  signal?: AbortSignal,
): Promise<HealthResponse> {
  const response = await fetch(backendUrls.healthUrl, {
    signal: signal ?? null,
  });

  if (!response.ok) {
    throw new Error('Backend health check failed.');
  }

  return healthResponseSchema.parse(await response.json());
}
