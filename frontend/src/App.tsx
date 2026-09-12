import { useQuery } from '@tanstack/react-query';

import { fetchHealth } from './api/health';
import { LiveRoomSession } from './features/session/LiveRoomSession';
import { demoTableForVariant } from './features/table/demoTable.fixture';
import { TableScreen } from './features/table/TableScreen';

function DemoApp({ variant }: { variant: 'active' | 'open' }) {
  const health = useQuery({
    queryKey: ['health'],
    queryFn: ({ signal }) => fetchHealth(signal),
  });

  const backendStatus = health.isPending
    ? 'checking'
    : health.isSuccess
      ? 'online'
      : 'offline';

  return (
    <TableScreen
      table={demoTableForVariant(variant)}
      backendStatus={backendStatus}
    />
  );
}

export function App() {
  const demoVariant = new URLSearchParams(window.location.search).get('demo');

  if (demoVariant === 'active' || demoVariant === 'open') {
    return <DemoApp variant={demoVariant} />;
  }

  return <LiveRoomSession />;
}
