import { createStore, type StoreApi } from 'zustand/vanilla';

import type { RoomView } from './messages';
import type { SessionError } from './sessionError';

export type ConnectionStatus =
  'idle' | 'connecting' | 'syncing' | 'connected' | 'disconnected';

export interface CommandErrorState {
  readonly commandId: string | null;
  readonly code: string;
  readonly message: string;
}

export interface RealtimeState {
  readonly status: ConnectionStatus;
  readonly guestId: string | null;
  readonly roomCode: string | null;
  readonly snapshot: RoomView | null;
  readonly lastConnectionError: SessionError | null;
  readonly lastCommandError: CommandErrorState | null;
  beginConnection(roomCode: string): void;
  receiveConnected(guestId: string, roomCode: string): void;
  replaceSnapshot(snapshot: RoomView): void;
  receiveCommandAck(commandId: string): void;
  receiveCommandError(error: CommandErrorState): void;
  receiveConnectionError(error: SessionError): void;
  markDisconnected(): void;
  resetSession(): void;
}

const initialData = {
  status: 'idle' as const,
  guestId: null,
  roomCode: null,
  snapshot: null,
  lastConnectionError: null,
  lastCommandError: null,
};

export type RealtimeStore = StoreApi<RealtimeState>;

export function createRealtimeStore(): RealtimeStore {
  return createStore<RealtimeState>()((set, get) => ({
    ...initialData,
    beginConnection(roomCode) {
      const sameRoom = get().roomCode === roomCode;
      set({
        status: 'connecting',
        roomCode,
        guestId: sameRoom ? get().guestId : null,
        snapshot: sameRoom ? get().snapshot : null,
        lastConnectionError: null,
        lastCommandError: sameRoom ? get().lastCommandError : null,
      });
    },
    receiveConnected(guestId, roomCode) {
      set({
        status: 'syncing',
        guestId,
        roomCode,
        lastConnectionError: null,
      });
    },
    replaceSnapshot(snapshot) {
      set({ status: 'connected', snapshot });
    },
    receiveCommandAck() {
      // Phase 10B has no pending command state and acknowledgements never alter poker state.
    },
    receiveCommandError(error) {
      set({ lastCommandError: error });
    },
    receiveConnectionError(error) {
      set({ status: 'disconnected', lastConnectionError: error });
    },
    markDisconnected() {
      set({ status: 'disconnected' });
    },
    resetSession() {
      set(initialData);
    },
  }));
}
