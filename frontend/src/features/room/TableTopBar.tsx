import { formatChips } from '../table/formatChips';
import type { TableDemoView } from '../table/table.types';

export type BackendHealthStatus = 'checking' | 'online' | 'offline';

interface TableTopBarProps {
  table: TableDemoView;
  backendStatus: BackendHealthStatus;
}

const backendLabels: Record<BackendHealthStatus, string> = {
  checking: 'Backend checking',
  online: 'Backend online',
  offline: 'Backend offline',
};

export function TableTopBar({ table, backendStatus }: TableTopBarProps) {
  return (
    <header className="table-topbar">
      <div className="table-topbar__brand-group">
        <h1 className="table-topbar__title">
          <a className="table-topbar__brand" href="/">
            StreetPoker
          </a>
        </h1>
        <span className="demo-badge">Demo table</span>
      </div>

      <div className="room-summary" aria-label="Room summary">
        <strong>{table.roomName}</strong>
        <span className="room-summary__divider" aria-hidden="true" />
        <span>
          Code <b>{table.roomCode}</b>
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
          className={`backend-status backend-status--${backendStatus}`}
          role="status"
        >
          <span className="backend-status__dot" aria-hidden="true" />
          {backendLabels[backendStatus]}
        </span>
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
      </div>
    </header>
  );
}
