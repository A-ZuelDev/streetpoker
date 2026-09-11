import { useQuery } from '@tanstack/react-query';

import { fetchHealth } from './api/health';
import { demoTableForVariant } from './features/table/demoTable.fixture';
import { TableScreen } from './features/table/TableScreen';

export function App() {
  const health = useQuery({
    queryKey: ['health'],
    queryFn: ({ signal }) => fetchHealth(signal),
  });

  const backendStatus = health.isPending
    ? 'checking'
    : health.isSuccess
      ? 'online'
      : 'offline';

  const demoVariant = new URLSearchParams(window.location.search).get('demo');

  return (
    <TableScreen
      table={demoTableForVariant(demoVariant)}
      backendStatus={backendStatus}
    />
  );
}
