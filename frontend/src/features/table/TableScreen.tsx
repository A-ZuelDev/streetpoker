import { useState } from 'react';

import { RoomPanel } from '../room/RoomPanel';
import { TableTopBar, type BackendHealthStatus } from '../room/TableTopBar';
import { ActionBar } from './ActionBar';
import { PokerTable } from './PokerTable';
import type { OccupiedSeatView, TableDemoView } from './table.types';
import './table.css';
import '../room/room.css';

interface TableScreenProps {
  table: TableDemoView;
  backendStatus: BackendHealthStatus;
}

export function TableScreen({ table, backendStatus }: TableScreenProps) {
  const [isRoomPanelOpen, setIsRoomPanelOpen] = useState(true);
  const hero = table.seats.find(
    (seat): seat is OccupiedSeatView => seat.kind === 'occupied' && seat.isHero,
  );

  if (hero === undefined) {
    throw new Error('The table demo requires a hero seat.');
  }

  return (
    <div
      className={`table-screen table-screen--panel-${isRoomPanelOpen ? 'open' : 'closed'}`}
      data-table-theme="green"
    >
      <TableTopBar table={table} backendStatus={backendStatus} />
      <div className="table-screen__body">
        <main className="table-screen__game">
          <PokerTable table={table} />
          <ActionBar
            hero={hero}
            legalActions={table.legalActions}
            canStartHand={table.roomPanel.canStartHand}
          />
        </main>
        <RoomPanel
          panel={table.roomPanel}
          chat={table.chat}
          isOpen={isRoomPanelOpen}
          onToggle={() => setIsRoomPanelOpen((isOpen) => !isOpen)}
        />
      </div>
    </div>
  );
}
