import { useState } from 'react';

import { RoomPanel } from '../room/RoomPanel';
import { TableTopBar, type BackendHealthStatus } from '../room/TableTopBar';
import { ActionBar } from './ActionBar';
import { PokerTable } from './PokerTable';
import type { OccupiedSeatView, TableDemoView } from './table.types';
import type { ConnectionStatus } from '../../realtime/realtimeStore';
import type { PokerActionRequest } from '../../realtime/pokerActions';
import './table.css';
import '../room/room.css';

interface TableScreenProps {
  table: TableDemoView;
  backendStatus?: BackendHealthStatus;
  connectionStatus?: ConnectionStatus;
  connectionError?: string | null;
  persistenceWarning?: boolean;
  onReconnect?: () => void;
  commandError?: string | null;
  onPokerAction?: (request: PokerActionRequest) => boolean;
}

export function TableScreen({
  table,
  backendStatus = 'checking',
  connectionStatus = 'idle',
  connectionError = null,
  persistenceWarning = false,
  onReconnect,
  commandError = null,
  onPokerAction,
}: TableScreenProps) {
  const [isRoomPanelOpen, setIsRoomPanelOpen] = useState(true);
  const hero = table.seats.find(
    (seat): seat is OccupiedSeatView => seat.kind === 'occupied' && seat.isHero,
  );

  if (table.mode === 'demo' && hero === undefined) {
    throw new Error('The table demo requires a hero seat.');
  }

  const liveIsFresh = table.mode === 'live' && connectionStatus === 'connected';
  const liveIsStale = table.mode === 'live' && !liveIsFresh;

  return (
    <div
      className={`table-screen table-screen--panel-${isRoomPanelOpen ? 'open' : 'closed'} ${liveIsStale ? 'table-screen--stale' : ''}`}
      data-table-theme="green"
    >
      <TableTopBar
        table={table}
        backendStatus={backendStatus}
        connectionStatus={connectionStatus}
        {...(onReconnect === undefined ? {} : { onReconnect })}
      />
      {liveIsStale || connectionError !== null || persistenceWarning ? (
        <div className="session-banner" role="status">
          <span>
            {connectionError ??
              (connectionStatus === 'syncing'
                ? 'Connected. Waiting for a fresh table update.'
                : connectionStatus === 'connecting'
                  ? 'Connecting. The displayed table may be stale.'
                  : 'Disconnected. The displayed table is stale.')}
          </span>
          {persistenceWarning ? (
            <small>
              Your browser session will not persist after this page closes.
            </small>
          ) : null}
        </div>
      ) : null}
      <div className="table-screen__body">
        <main className="table-screen__game">
          <PokerTable table={table} />
          <ActionBar
            hero={hero ?? null}
            legalActions={table.legalActions}
            canStartHand={table.roomPanel.canStartHand}
            mode={table.mode}
            isHandActive={table.isHandActive}
            liveActions={table.liveActions}
            commandError={commandError}
            {...(onPokerAction === undefined ? {} : { onPokerAction })}
          />
        </main>
        <RoomPanel
          panel={table.roomPanel}
          chat={table.chat}
          mode={table.mode}
          isOpen={isRoomPanelOpen}
          onToggle={() => setIsRoomPanelOpen((isOpen) => !isOpen)}
        />
      </div>
    </div>
  );
}
