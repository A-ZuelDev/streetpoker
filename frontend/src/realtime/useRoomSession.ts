import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { canonicalizeRoomCode, type BackendUrls } from '../api/backendUrls';
import {
  createRoom as createRoomRequest,
  type CreateRoomRequest,
  type CreateRoomResponse,
} from '../api/rooms';
import { backendUrls } from '../env';
import { getOrCreateGuestToken, type GuestTokenResult } from './guestToken';
import type { ConnectFrame, ServerMessage } from './messages';
import {
  RoomSocket,
  type RoomSocketFactory,
  type RoomSocketOptions,
} from './roomSocket';
import type { RealtimeStore } from './realtimeStore';
import {
  connectionSessionError,
  SessionError,
  protocolSessionError,
} from './sessionError';

export interface CreateRoomInput {
  readonly nickname: string;
  readonly roomName: string;
  readonly smallBlind: number;
  readonly bigBlind: number;
  readonly startingStack: number;
  readonly seatingApprovalRequired: boolean;
  readonly password?: string;
}

export interface JoinRoomInput {
  readonly roomCode: string;
  readonly nickname: string;
  readonly password?: string;
}

export interface SessionDependencies {
  readonly urls?: BackendUrls;
  readonly createRoomClient?: (
    request: CreateRoomRequest,
  ) => Promise<CreateRoomResponse>;
  readonly roomSocketFactory?: RoomSocketFactory;
  readonly guestTokenProvider?: () => GuestTokenResult;
}

function unexpectedSessionError(): SessionError {
  return new SessionError({
    source: 'network',
    code: 'session_error',
    message: 'The room session could not be started.',
  });
}

export function useRoomSession(
  store: RealtimeStore,
  dependencies: SessionDependencies = {},
) {
  const urls = dependencies.urls ?? backendUrls;
  const socketFactory = useMemo<RoomSocketFactory>(
    () =>
      dependencies.roomSocketFactory ??
      ((options: RoomSocketOptions) => new RoomSocket(options)),
    [dependencies.roomSocketFactory],
  );
  const tokenProvider =
    dependencies.guestTokenProvider ?? getOrCreateGuestToken;
  const roomClient = dependencies.createRoomClient ?? createRoomRequest;
  const socketRef = useRef<RoomSocket | null>(null);
  const generationRef = useRef(0);
  const inFlightRef = useRef(false);
  const confirmedMembershipRef = useRef(false);
  const mountedRef = useRef(true);
  const [entryError, setEntryError] = useState<SessionError | null>(null);
  const [persistenceWarning, setPersistenceWarning] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
  const [canReconnect, setCanReconnect] = useState(false);

  const stableToken = useCallback(() => {
    const result = tokenProvider();
    if (mountedRef.current) {
      setPersistenceWarning(!result.persistent);
    }
    return result.token;
  }, [tokenProvider]);

  const openSocket = useCallback(
    (roomCode: string, connectFrame: ConnectFrame) => {
      const canonicalRoomCode = canonicalizeRoomCode(roomCode);
      generationRef.current += 1;
      const generation = generationRef.current;
      const previous = socketRef.current;
      socketRef.current = null;
      previous?.close();
      store.getState().beginConnection(canonicalRoomCode);
      setEntryError(null);

      let candidate: RoomSocket | null = null;
      const isCurrent = () =>
        mountedRef.current &&
        generationRef.current === generation &&
        candidate !== null &&
        socketRef.current === candidate;
      const rejectProtocol = () => {
        if (!isCurrent()) {
          return;
        }
        const error = protocolSessionError();
        store.getState().receiveConnectionError(error);
        setEntryError(error);
        inFlightRef.current = false;
        candidate?.close();
      };
      const receiveMessage = (message: ServerMessage) => {
        if (!isCurrent()) {
          return;
        }
        const state = store.getState();
        switch (message.type) {
          case 'connected':
            if (message.room_code !== canonicalRoomCode) {
              rejectProtocol();
              return;
            }
            state.receiveConnected(message.guest_id, message.room_code);
            confirmedMembershipRef.current = true;
            setCanReconnect(true);
            inFlightRef.current = false;
            return;
          case 'state':
            if (
              message.snapshot.room.room_code !== canonicalRoomCode ||
              (state.status !== 'syncing' && state.status !== 'connected')
            ) {
              rejectProtocol();
              return;
            }
            state.replaceSnapshot(message.snapshot);
            return;
          case 'command_ack':
            state.receiveCommandAck(message.command_id);
            return;
          case 'command_error':
            state.receiveCommandError({
              commandId: message.command_id,
              code: message.code,
              message: 'The server rejected a room command.',
            });
            return;
          case 'connection_error': {
            const error = connectionSessionError(message.code);
            state.receiveConnectionError(error);
            setEntryError(error);
            inFlightRef.current = false;
            candidate?.close();
            return;
          }
        }
      };

      try {
        candidate = socketFactory({
          urls,
          roomCode: canonicalRoomCode,
          connectFrame,
          events: {
            onMessage: receiveMessage,
            onFailure(error) {
              if (!isCurrent()) {
                return;
              }
              store.getState().receiveConnectionError(error);
              setEntryError(error);
              inFlightRef.current = false;
              candidate?.close();
            },
            onClose() {
              if (!isCurrent()) {
                return;
              }
              inFlightRef.current = false;
              store.getState().markDisconnected();
            },
          },
        });
        socketRef.current = candidate;
      } catch {
        const error = unexpectedSessionError();
        store.getState().receiveConnectionError(error);
        setEntryError(error);
        inFlightRef.current = false;
      }
    },
    [socketFactory, store, urls],
  );

  const createRoom = useCallback(
    async (input: CreateRoomInput) => {
      if (inFlightRef.current) {
        return;
      }
      inFlightRef.current = true;
      setIsCreating(true);
      confirmedMembershipRef.current = false;
      setCanReconnect(false);
      setEntryError(null);
      try {
        const token = stableToken();
        const request: CreateRoomRequest = {
          guest_token: token,
          nickname: input.nickname,
          settings: {
            room_name: input.roomName,
            small_blind: input.smallBlind,
            big_blind: input.bigBlind,
            default_starting_stack: input.startingStack,
            seating_approval_required: input.seatingApprovalRequired,
          },
          ...(input.password === undefined ? {} : { password: input.password }),
        };
        const response = await roomClient(request);
        if (!mountedRef.current) {
          inFlightRef.current = false;
          return;
        }
        setIsCreating(false);
        confirmedMembershipRef.current = true;
        setCanReconnect(true);
        openSocket(response.room_code, {
          type: 'connect',
          guest_token: token,
        });
      } catch (error) {
        inFlightRef.current = false;
        if (mountedRef.current) {
          setIsCreating(false);
          setEntryError(
            error instanceof SessionError ? error : unexpectedSessionError(),
          );
        }
      }
    },
    [openSocket, roomClient, stableToken],
  );

  const joinRoom = useCallback(
    (input: JoinRoomInput) => {
      if (inFlightRef.current) {
        return;
      }
      inFlightRef.current = true;
      try {
        const roomCode = canonicalizeRoomCode(input.roomCode);
        const token = stableToken();
        confirmedMembershipRef.current = false;
        setCanReconnect(false);
        openSocket(roomCode, {
          type: 'connect',
          guest_token: token,
          nickname: input.nickname,
          ...(input.password === undefined ? {} : { password: input.password }),
        });
      } catch (error) {
        inFlightRef.current = false;
        setEntryError(
          error instanceof SessionError ? error : unexpectedSessionError(),
        );
      }
    },
    [openSocket, stableToken],
  );

  const reconnect = useCallback(() => {
    if (inFlightRef.current || !confirmedMembershipRef.current) {
      return;
    }
    const roomCode = store.getState().roomCode;
    if (roomCode === null) {
      return;
    }
    inFlightRef.current = true;
    openSocket(roomCode, {
      type: 'connect',
      guest_token: stableToken(),
    });
  }, [openSocket, stableToken, store]);

  const disconnect = useCallback(() => {
    inFlightRef.current = false;
    generationRef.current += 1;
    const socket = socketRef.current;
    socketRef.current = null;
    socket?.close();
    if (store.getState().status !== 'idle') {
      store.getState().markDisconnected();
    }
  }, [store]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      generationRef.current += 1;
      const socket = socketRef.current;
      socketRef.current = null;
      socket?.close();
    };
  }, []);

  return {
    createRoom,
    joinRoom,
    reconnect,
    disconnect,
    entryError,
    persistenceWarning,
    isCreating,
    canReconnect,
  };
}
