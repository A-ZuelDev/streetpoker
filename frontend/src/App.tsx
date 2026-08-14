import { useQuery } from '@tanstack/react-query';

import { fetchHealth } from './api/health';

export function App() {
  const health = useQuery({
    queryKey: ['health'],
    queryFn: ({ signal }) => fetchHealth(signal),
  });

  const status = health.isPending
    ? 'Checking backend...'
    : health.isSuccess
      ? 'Backend connected'
      : 'Backend unavailable';

  return (
    <main>
      <section className="hero" aria-labelledby="page-title">
        <p className="eyebrow">Phase 0 - Foundation</p>
        <h1 id="page-title">StreetPoker</h1>
        <p className="lede">
          A server-authoritative, browser-based play-money poker platform.
        </p>
        <div className="status-card" aria-live="polite">
          <span
            className={`status-dot ${health.isSuccess ? 'status-dot--online' : ''}`}
            aria-hidden="true"
          />
          <span>{status}</span>
        </div>
      </section>
    </main>
  );
}
