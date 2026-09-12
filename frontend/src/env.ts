import { z } from 'zod';

import { createBackendUrls } from './api/backendUrls';

const environmentSchema = z.strictObject({
  VITE_API_BASE_URL: z.string().min(1),
});

const environment = environmentSchema.parse({
  VITE_API_BASE_URL:
    import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000',
});

export const backendUrls = createBackendUrls(environment.VITE_API_BASE_URL);
