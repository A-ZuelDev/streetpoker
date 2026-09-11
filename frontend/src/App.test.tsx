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
  window.history.replaceState({}, '', '/');
});

describe('App', () => {
  it('shows the active demo table and backend health independently', async () => {
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
    expect(screen.getByText('Demo table')).toBeInTheDocument();
    expect(
      screen.getByRole('region', { name: 'Six-max poker table' }),
    ).toBeInTheDocument();
    expect(await screen.findByText('Backend online')).toBeInTheDocument();
  });

  it('reports backend health failure without implying the table is live', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));

    renderApp();

    expect(await screen.findByText('Backend offline')).toBeInTheDocument();
    expect(screen.getByText('Demo table')).toBeInTheDocument();
    expect(screen.queryByText(/websocket/i)).not.toBeInTheDocument();
  });

  it('selects the open demo from the query string', () => {
    window.history.replaceState({}, '', '/?demo=open');
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));

    renderApp();

    expect(screen.getByText('Table ready')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Start hand' }),
    ).toBeInTheDocument();
  });
});
