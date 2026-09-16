import { useEffect, useState } from 'react';

import { formatChips } from '../table/formatChips';
import type { TableDemoView } from '../table/table.types';
import type { ConnectionStatus } from '../../realtime/realtimeStore';

export type BackendHealthStatus = 'checking' | 'online' | 'offline';

interface TableTopBarProps {
  table: TableDemoView;
  backendStatus: BackendHealthStatus;
  connectionStatus: ConnectionStatus;
  onReconnect?: () => void;
}

const backendLabels: Record<BackendHealthStatus, string> = {
  checking: 'Backend checking',
  online: 'Backend online',
  offline: 'Backend offline',
};

const connectionLabels: Record<ConnectionStatus, string> = {
  idle: 'Not connected',
  connecting: 'Connecting',
  syncing: 'Syncing table',
  connected: 'Table live',
  disconnected: 'Disconnected · stale table',
};

export function TableTopBar({
  table,
  backendStatus,
  connectionStatus,
  onReconnect,
}: TableTopBarProps) {
  const [copyStatus, setCopyStatus] = useState<'copied' | 'unavailable' | null>(
    null,
  );
  const statusClass = table.mode === 'demo' ? backendStatus : connectionStatus;
  const statusLabel =
    table.mode === 'demo'
      ? backendLabels[backendStatus]
      : connectionLabels[connectionStatus];

  useEffect(() => {
    if (copyStatus === null) {
      return;
    }
    const timeout = window.setTimeout(() => setCopyStatus(null), 2_000);
    return () => window.clearTimeout(timeout);
  }, [copyStatus]);

  const copyRoomCode = async () => {
    try {
      if (navigator.clipboard === undefined) {
        throw new Error('Clipboard unavailable');
      }
      await navigator.clipboard.writeText(table.roomCode);
      setCopyStatus('copied');
    } catch {
      setCopyStatus('unavailable');
    }
  };

  return (
    <header className="table-topbar">
      <div className="table-topbar__brand-group">
        <h1 className="table-topbar__title">
          {table.mode === 'live' ? (
            <span className="table-topbar__brand">StreetPoker</span>
          ) : (
            <a className="table-topbar__brand" href="/">
              StreetPoker
            </a>
          )}
        </h1>
        <span className="demo-badge">
          {table.mode === 'demo' ? 'Demo table' : 'Live room'}
        </span>
      </div>

      <div className="room-summary" aria-label="Room summary">
        <strong>{table.roomName}</strong>
        <span className="room-summary__divider" aria-hidden="true" />
        <span className="room-summary__code">
          Code <code>{table.roomCode}</code>
          <button
            className="room-summary__copy"
            type="button"
            onClick={() => void copyRoomCode()}
          >
            Copy code
          </button>
          <span className="room-summary__copy-status" role="status">
            {copyStatus === 'copied'
              ? 'Copied'
              : copyStatus === 'unavailable'
                ? 'Copy unavailable'
                : null}
          </span>
        </span>
        <span className="room-summary__divider" aria-hidden="true" />
        <span>
          {formatChips(table.smallBlind)} / {formatChips(table.bigBlind)}
        </span>
      </div>

      <div className="table-topbar__actions">
        {table.isHost ? (
          <span className="host-badge" aria-label="You are the room host">
            Host
          </span>
        ) : null}
        <span
          className={`backend-status backend-status--${statusClass}`}
          role="status"
        >
          <span className="backend-status__dot" aria-hidden="true" />
          {statusLabel}
        </span>
        {table.mode === 'live' && connectionStatus === 'disconnected' ? (
          <button className="topbar-button" type="button" onClick={onReconnect}>
            Reconnect
          </button>
        ) : null}
        {table.mode === 'demo' ? (
          <>
            <button
              className="topbar-button"
              type="button"
              disabled
              title="Demo only"
            >
              Settings
            </button>
            <button
              className="topbar-button topbar-button--leave"
              type="button"
              disabled
              title="Demo only"
            >
              Leave
            </button>
          </>
        ) : null}
      </div>
    </header>
  );
}
