import { useState } from 'react';

import { RoomPanel } from '../room/RoomPanel';
import { TableTopBar, type BackendHealthStatus } from '../room/TableTopBar';
import { ActionBar } from './ActionBar';
import { PokerTable } from './PokerTable';
import type { OccupiedSeatView, TableDemoView } from './table.types';
import type { ConnectionStatus } from '../../realtime/realtimeStore';
import type { PokerActionRequest } from '../../realtime/pokerActions';
import type { RoomCommandRequest } from '../../realtime/roomCommands';
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
  roomCommandError?: string | null;
  onPokerAction?: (request: PokerActionRequest) => boolean;
  onRoomCommand?: (request: RoomCommandRequest) => boolean;
}

function roomPanelStartsOpen(): boolean {
  return (
    typeof window.matchMedia !== 'function' ||
    !window.matchMedia('(max-width: 1120px)').matches
  );
}

export function TableScreen({
  table,
  backendStatus = 'checking',
  connectionStatus = 'idle',
  connectionError = null,
  persistenceWarning = false,
  onReconnect,
  commandError = null,
  roomCommandError = null,
  onPokerAction,
  onRoomCommand,
}: TableScreenProps) {
  const [isRoomPanelOpen, setIsRoomPanelOpen] = useState(roomPanelStartsOpen);
  const hero = table.seats.find(
    (seat): seat is OccupiedSeatView => seat.kind === 'occupied' && seat.isHero,
  );

  if (table.mode === 'demo' && hero === undefined) {
    throw new Error('The table demo requires a hero seat.');
  }

  const liveIsFresh = table.mode === 'live' && connectionStatus === 'connected';
  const liveIsStale = table.mode === 'live' && !liveIsFresh;
  const connectionNotice = liveIsStale
    ? (connectionError ??
      (connectionStatus === 'syncing'
        ? 'Connected. Waiting for a fresh table update.'
        : connectionStatus === 'connecting'
          ? 'Connecting. The displayed table may be stale.'
          : 'Disconnected. The displayed table is stale.'))
    : null;
  const roomPending = table.roomPanel.pendingCommand ?? null;
  const hasNotices =
    connectionNotice !== null ||
    persistenceWarning ||
    roomPending !== null ||
    roomCommandError !== null;

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
      {hasNotices ? (
        <div className="table-notices" aria-label="Table notices">
          {connectionNotice === null ? null : (
            <div className="session-banner session-banner--connection">
              {connectionNotice}
            </div>
          )}
          {persistenceWarning ? (
            <div
              className="session-banner session-banner--warning"
              role="status"
            >
              Your browser session will not persist after this page closes.
            </div>
          ) : null}
          {roomPending === null ? null : (
            <div
              className="session-banner session-banner--pending"
              role="status"
              aria-atomic="true"
            >
              {roomPending.label}
            </div>
          )}
          {roomCommandError === null ? null : (
            <div className="session-banner session-banner--error" role="alert">
              {roomCommandError}
            </div>
          )}
        </div>
      ) : null}
      <div className="table-screen__body">
        <main className="table-screen__game">
          <PokerTable
            table={table}
            {...(onRoomCommand === undefined
              ? {}
              : {
                  onRequestSeat: (seatIndex: number) =>
                    onRoomCommand({ type: 'request_seat', seatIndex }),
                })}
          />
          <ActionBar
            hero={hero ?? null}
            legalActions={table.legalActions}
            canStartHand={table.roomPanel.canStartHand}
            isHost={table.isHost}
            mode={table.mode}
            handCompletion={table.handCompletion}
            liveActions={table.liveActions}
            commandError={commandError}
            {...(onPokerAction === undefined ? {} : { onPokerAction })}
            {...(onRoomCommand === undefined
              ? {}
              : {
                  onStartHand: () => onRoomCommand({ type: 'start_hand' }),
                })}
          />
        </main>
        <RoomPanel
          panel={table.roomPanel}
          chat={table.chat}
          mode={table.mode}
          isOpen={isRoomPanelOpen}
          onToggle={() => setIsRoomPanelOpen((isOpen) => !isOpen)}
          roomCode={table.roomCode}
          {...(onRoomCommand === undefined ? {} : { onRoomCommand })}
        />
      </div>
    </div>
  );
}
