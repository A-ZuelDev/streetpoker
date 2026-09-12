import { createStore, type StoreApi } from 'zustand/vanilla';

import type { RoomView } from './messages';
import type { SessionError } from './sessionError';
import type { PendingPokerCommand } from './pokerActions';

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
  readonly pendingCommand: PendingPokerCommand | null;
  beginConnection(roomCode: string): void;
  receiveConnected(guestId: string, roomCode: string): void;
  replaceSnapshot(snapshot: RoomView): void;
  receiveCommandAck(commandId: string): void;
  receiveCommandError(error: CommandErrorState): void;
  tryBeginPokerCommand(command: PendingPokerCommand): boolean;
  cancelPendingPokerCommand(commandId: string, error?: CommandErrorState): void;
  reportLocalCommandError(error: CommandErrorState): void;
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
  pendingCommand: null,
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
        pendingCommand: null,
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
      const pending = get().pendingCommand;
      const hand = snapshot.active_hand;
      const stillPending =
        pending !== null &&
        hand !== null &&
        hand.hand_number === pending.handNumber &&
        hand.action_sequence === pending.expectedActionSequence &&
        hand.current_actor === pending.actorGuestId;
      set({
        status: 'connected',
        snapshot,
        pendingCommand: stillPending ? pending : null,
      });
    },
    receiveCommandAck() {
      // An acknowledgement never alters poker state or resolves pending UI.
    },
    receiveCommandError(error) {
      const pending = get().pendingCommand;
      if (pending === null || error.commandId !== pending.commandId) {
        return;
      }
      set({ pendingCommand: null, lastCommandError: error });
    },
    tryBeginPokerCommand(command) {
      if (get().pendingCommand !== null) {
        return false;
      }
      set({ pendingCommand: command, lastCommandError: null });
      return true;
    },
    cancelPendingPokerCommand(commandId, error) {
      if (get().pendingCommand?.commandId !== commandId) {
        return;
      }
      set({
        pendingCommand: null,
        ...(error === undefined ? {} : { lastCommandError: error }),
      });
    },
    reportLocalCommandError(error) {
      set({ lastCommandError: error });
    },
    receiveConnectionError(error) {
      set({
        status: 'disconnected',
        lastConnectionError: error,
        pendingCommand: null,
      });
    },
    markDisconnected() {
      set({ status: 'disconnected', pendingCommand: null });
    },
    resetSession() {
      set(initialData);
    },
  }));
}
