import { z } from 'zod';

import { environment } from '../env';

const healthResponseSchema = z.object({
  status: z.literal('ok'),
});

export type HealthResponse = z.infer<typeof healthResponseSchema>;

export async function fetchHealth(
  signal?: AbortSignal,
): Promise<HealthResponse> {
  const response = await fetch(`${environment.VITE_API_BASE_URL}/health`, {
    signal: signal ?? null,
  });

  if (!response.ok) {
    throw new Error('Backend health check failed.');
  }

  return healthResponseSchema.parse(await response.json());
}
