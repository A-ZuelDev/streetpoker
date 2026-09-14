import { createStore, type StoreApi } from 'zustand/vanilla';

import type { RoomView } from './messages';
import type { SessionError } from './sessionError';
import type { PendingPokerCommand } from './pokerActions';
import type { PendingRoomCommand } from './roomCommands';

export type ConnectionStatus =
  'idle' | 'connecting' | 'syncing' | 'connected' | 'disconnected';

export interface CommandErrorState {
  readonly commandId: string | null;
  readonly code: string;
  readonly message: string;
}

export interface RoomExitState {
  readonly kind: 'left' | 'kicked' | 'closed';
  readonly roomCode: string;
  readonly roomName: string | null;
}

export interface RealtimeState {
  readonly status: ConnectionStatus;
  readonly guestId: string | null;
  readonly roomCode: string | null;
  readonly snapshot: RoomView | null;
  readonly lastConnectionError: SessionError | null;
  readonly lastPokerCommandError: CommandErrorState | null;
  readonly lastRoomCommandError: CommandErrorState | null;
  readonly pendingPokerCommand: PendingPokerCommand | null;
  readonly pendingRoomCommand: PendingRoomCommand | null;
  readonly roomExit: RoomExitState | null;
  beginConnection(roomCode: string): void;
  receiveConnected(guestId: string, roomCode: string): void;
  replaceSnapshot(snapshot: RoomView): void;
  receiveCommandAck(commandId: string): void;
  receivePokerCommandError(error: CommandErrorState): void;
  receiveRoomCommandError(error: CommandErrorState): void;
  tryBeginPokerCommand(command: PendingPokerCommand): boolean;
  tryBeginRoomCommand(command: PendingRoomCommand): boolean;
  cancelPendingPokerCommand(commandId: string, error?: CommandErrorState): void;
  cancelPendingRoomCommand(commandId: string, error?: CommandErrorState): void;
  reportLocalPokerCommandError(error: CommandErrorState): void;
  reportLocalRoomCommandError(error: CommandErrorState): void;
  endRoomSession(exit: RoomExitState): void;
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
  lastPokerCommandError: null,
  lastRoomCommandError: null,
  pendingPokerCommand: null,
  pendingRoomCommand: null,
  roomExit: null,
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
        lastPokerCommandError: sameRoom ? get().lastPokerCommandError : null,
        lastRoomCommandError: sameRoom ? get().lastRoomCommandError : null,
        pendingPokerCommand: null,
        pendingRoomCommand: null,
        roomExit: null,
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
      const pokerPending = get().pendingPokerCommand;
      const hand = snapshot.active_hand;
      const pokerStillPending =
        pokerPending !== null &&
        hand !== null &&
        hand.hand_number === pokerPending.handNumber &&
        hand.action_sequence === pokerPending.expectedActionSequence &&
        hand.current_actor === pokerPending.actorGuestId;
      const roomPending = get().pendingRoomCommand;
      set({
        status: 'connected',
        snapshot,
        pendingPokerCommand: pokerStillPending ? pokerPending : null,
        pendingRoomCommand:
          roomPending?.acknowledged === true ? null : roomPending,
      });
    },
    receiveCommandAck(commandId) {
      const pending = get().pendingRoomCommand;
      if (pending?.commandId !== commandId) {
        // Poker acknowledgements never alter poker state or pending UI.
        return;
      }
      set({ pendingRoomCommand: { ...pending, acknowledged: true } });
    },
    receivePokerCommandError(error) {
      const pending = get().pendingPokerCommand;
      if (pending === null || error.commandId !== pending.commandId) {
        return;
      }
      set({ pendingPokerCommand: null, lastPokerCommandError: error });
    },
    receiveRoomCommandError(error) {
      const pending = get().pendingRoomCommand;
      if (pending === null || error.commandId !== pending.commandId) {
        return;
      }
      set({ pendingRoomCommand: null, lastRoomCommandError: error });
    },
    tryBeginPokerCommand(command) {
      if (
        get().pendingPokerCommand !== null ||
        get().pendingRoomCommand !== null
      ) {
        return false;
      }
      set({ pendingPokerCommand: command, lastPokerCommandError: null });
      return true;
    },
    tryBeginRoomCommand(command) {
      if (
        get().pendingPokerCommand !== null ||
        get().pendingRoomCommand !== null
      ) {
        return false;
      }
      set({ pendingRoomCommand: command, lastRoomCommandError: null });
      return true;
    },
    cancelPendingPokerCommand(commandId, error) {
      if (get().pendingPokerCommand?.commandId !== commandId) {
        return;
      }
      set({
        pendingPokerCommand: null,
        ...(error === undefined ? {} : { lastPokerCommandError: error }),
      });
    },
    cancelPendingRoomCommand(commandId, error) {
      if (get().pendingRoomCommand?.commandId !== commandId) {
        return;
      }
      set({
        pendingRoomCommand: null,
        ...(error === undefined ? {} : { lastRoomCommandError: error }),
      });
    },
    reportLocalPokerCommandError(error) {
      set({ lastPokerCommandError: error });
    },
    reportLocalRoomCommandError(error) {
      set({ lastRoomCommandError: error });
    },
    endRoomSession(exit) {
      set({
        ...initialData,
        roomExit: exit,
      });
    },
    receiveConnectionError(error) {
      set({
        status: 'disconnected',
        lastConnectionError: error,
        pendingPokerCommand: null,
        pendingRoomCommand: null,
      });
    },
    markDisconnected() {
      set({
        status: 'disconnected',
        pendingPokerCommand: null,
        pendingRoomCommand: null,
      });
    },
    resetSession() {
      set(initialData);
    },
  }));
}
