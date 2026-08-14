import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { App } from './App';
import { AppProviders } from './app/providers';

function renderApp() {
  return render(
    <AppProviders>
      <App />
    </AppProviders>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('App', () => {
  it('shows the project identity and a healthy backend', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ status: 'ok' }),
      }),
    );

    renderApp();

    expect(
      screen.getByRole('heading', { name: 'StreetPoker' }),
    ).toBeInTheDocument();
    expect(await screen.findByText('Backend connected')).toBeInTheDocument();
  });

  it('reports an unavailable backend without crashing', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));

    renderApp();

    expect(await screen.findByText('Backend unavailable')).toBeInTheDocument();
  });
});
