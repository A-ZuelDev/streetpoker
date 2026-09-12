import { useState } from 'react';
import { useStore } from 'zustand';

import { createRealtimeStore } from '../../realtime/realtimeStore';
import { useRoomSession } from '../../realtime/useRoomSession';
import { TableScreen } from '../table/TableScreen';
import { roomSnapshotToTableView } from '../table/roomSnapshotAdapter';
import { RoomEntry } from './RoomEntry';
import './session.css';

export function LiveRoomSession() {
  const [store] = useState(createRealtimeStore);
  const session = useRoomSession(store);
  const state = useStore(store, (current) => current);
  const busy =
    session.isCreating ||
    state.status === 'connecting' ||
    state.status === 'syncing';

  if (state.snapshot !== null && state.guestId !== null) {
    return (
      <TableScreen
        table={roomSnapshotToTableView(
          state.snapshot,
          state.guestId,
          state.status,
          state.pendingCommand,
        )}
        connectionStatus={state.status}
        connectionError={state.lastConnectionError?.message ?? null}
        persistenceWarning={session.persistenceWarning}
        onReconnect={session.reconnect}
        commandError={state.lastCommandError?.message ?? null}
        onPokerAction={session.sendPokerAction}
      />
    );
  }

  const statusText =
    state.status === 'connecting'
      ? 'Connecting to the room…'
      : state.status === 'syncing'
        ? 'Connected. Syncing the authoritative table…'
        : state.status === 'disconnected'
          ? session.canReconnect
            ? 'Disconnected before the table finished syncing.'
            : 'The connection closed. Check the room details and try again.'
          : null;

  return (
    <RoomEntry
      busy={busy}
      statusText={statusText}
      error={session.entryError}
      persistenceWarning={session.persistenceWarning}
      canReconnect={session.canReconnect}
      onCreate={session.createRoom}
      onJoin={session.joinRoom}
      onReconnect={session.reconnect}
    />
  );
}
