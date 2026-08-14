import { z } from 'zod';

import { environment } from '../env';

const healthResponseSchema = z.object({
  status: z.literal('ok'),
});

export type HealthResponse = z.infer<typeof healthResponseSchema>;

export function buildHealthUrl(apiBaseUrl: string): string {
  return `${apiBaseUrl.replace(/\/+$/, '')}/health`;
}

export async function fetchHealth(
  signal?: AbortSignal,
): Promise<HealthResponse> {
  const response = await fetch(buildHealthUrl(environment.VITE_API_BASE_URL), {
    signal: signal ?? null,
  });

  if (!response.ok) {
    throw new Error('Backend health check failed.');
  }

  return healthResponseSchema.parse(await response.json());
}
