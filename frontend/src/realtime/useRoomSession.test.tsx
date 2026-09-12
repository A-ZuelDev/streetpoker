import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import { StrictMode, type PropsWithChildren } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { createBackendUrls } from '../api/backendUrls';
import { activeRoomSnapshot, openRoomSnapshot } from '../test/roomSnapshots';
import type { ServerMessage } from './messages';
import type { RoomSocket, RoomSocketOptions } from './roomSocket';
import { createRealtimeStore } from './realtimeStore';
import {
  useRoomSession,
  type CreateRoomInput,
  type SessionDependencies,
} from './useRoomSession';

const token = 'A'.repeat(43);
const createInput: CreateRoomInput = {
  nickname: 'Mara',
  roomName: 'Friday Night',
  smallBlind: 50,
  bigBlind: 100,
  startingStack: 10_000,
  seatingApprovalRequired: true,
  password: 'private-password',
};

class FakeRoomSocket {
  closed = false;

  constructor(readonly options: RoomSocketOptions) {}

  close() {
    this.closed = true;
  }

  message(message: ServerMessage) {
    this.options.events.onMessage(message);
  }

  failure() {
    this.options.events.onFailure(new Error('not used') as never);
  }

  serverClose() {
    this.options.events.onClose();
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function setup(overrides: Partial<SessionDependencies> = {}) {
  const sockets: FakeRoomSocket[] = [];
  const roomSocketFactory = vi.fn((options: RoomSocketOptions) => {
    const socket = new FakeRoomSocket(options);
    sockets.push(socket);
    return socket as unknown as RoomSocket;
  });
  const store = createRealtimeStore();
  const dependencies: SessionDependencies = {
    urls: createBackendUrls('https://example.test/api'),
    guestTokenProvider: () => ({ token, persistent: true }),
    createRoomClient: vi.fn().mockResolvedValue({ room_code: 'ABCDEFGH' }),
    roomSocketFactory,
    ...overrides,
  };
  const queryClient = new QueryClient({
    defaultOptions: { mutations: { retry: false } },
  });
  const wrapper = ({ children }: PropsWithChildren) => (
    <StrictMode>
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    </StrictMode>
  );
  const hook = renderHook(() => useRoomSession(store, dependencies), {
    wrapper,
  });
  return { ...hook, store, dependencies, sockets, roomSocketFactory };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('useRoomSession', () => {
  it('does not create HTTP requests or sockets from StrictMode effects', () => {
    const setupResult = setup();

    expect(setupResult.dependencies.createRoomClient).not.toHaveBeenCalled();
    expect(setupResult.roomSocketFactory).not.toHaveBeenCalled();
    expect(setupResult.store.getState().status).toBe('idle');
  });

  it('guards a synchronous duplicate create and uses one token for HTTP and WS', async () => {
    const pending = deferred<{ room_code: string }>();
    const createRoomClient = vi.fn(() => pending.promise);
    const setupResult = setup({ createRoomClient });

    act(() => {
      void setupResult.result.current.createRoom(createInput);
      void setupResult.result.current.createRoom(createInput);
    });

    await waitFor(() => expect(createRoomClient).toHaveBeenCalledOnce());
    expect(createRoomClient).toHaveBeenCalledWith(
      expect.objectContaining({
        guest_token: token,
        password: 'private-password',
      }),
    );

    await act(async () => {
      pending.resolve({ room_code: 'ABCDEFGH' });
      await pending.promise;
    });

    await waitFor(() => expect(setupResult.sockets).toHaveLength(1));
    expect(setupResult.sockets[0]!.options.connectFrame).toEqual({
      type: 'connect',
      guest_token: token,
    });
  });

  it('moves connecting to syncing and only state to connected', () => {
    const setupResult = setup();

    act(() => {
      setupResult.result.current.joinRoom({
        roomCode: ' abcdefgh ',
        nickname: 'Mara',
        password: 'private-password',
      });
    });
    const socket = setupResult.sockets[0]!;
    expect(setupResult.store.getState().status).toBe('connecting');
    expect(socket.options.connectFrame).toEqual({
      type: 'connect',
      guest_token: token,
      nickname: 'Mara',
      password: 'private-password',
    });

    act(() => {
      socket.message({
        type: 'connected',
        guest_id: 'guest_host',
        room_code: 'ABCDEFGH',
      });
    });
    expect(setupResult.store.getState().status).toBe('syncing');

    act(() => {
      socket.message({ type: 'state', snapshot: openRoomSnapshot() });
    });
    expect(setupResult.store.getState().status).toBe('connected');
  });

  it('ignores late old-generation messages and closes during replacement', () => {
    const setupResult = setup();
    act(() => {
      setupResult.result.current.joinRoom({
        roomCode: 'ABCDEFGH',
        nickname: 'Mara',
      });
    });
    const oldSocket = setupResult.sockets[0]!;
    act(() => {
      oldSocket.message({
        type: 'connected',
        guest_id: 'guest_host',
        room_code: 'ABCDEFGH',
      });
      oldSocket.message({
        type: 'state',
        snapshot: openRoomSnapshot('Old room'),
      });
      oldSocket.serverClose();
      setupResult.result.current.reconnect();
    });
    const currentSocket = setupResult.sockets[1]!;
    expect(oldSocket.closed).toBe(true);
    expect(currentSocket.options.connectFrame).toEqual({
      type: 'connect',
      guest_token: token,
    });

    act(() => {
      currentSocket.message({
        type: 'connected',
        guest_id: 'guest_host',
        room_code: 'ABCDEFGH',
      });
    });
    expect(setupResult.store.getState()).toMatchObject({
      status: 'syncing',
      snapshot: { room: { settings: { room_name: 'Old room' } } },
    });

    act(() => {
      oldSocket.message({
        type: 'state',
        snapshot: activeRoomSnapshot('Late old'),
      });
      oldSocket.serverClose();
    });
    expect(setupResult.store.getState()).toMatchObject({
      status: 'syncing',
      snapshot: { room: { settings: { room_name: 'Old room' } } },
    });

    act(() => {
      currentSocket.message({
        type: 'state',
        snapshot: activeRoomSnapshot('Fresh room'),
      });
    });
    expect(setupResult.store.getState()).toMatchObject({
      status: 'connected',
      snapshot: { room: { settings: { room_name: 'Fresh room' } } },
    });
  });

  it('rejects state before connected and maps typed connection errors safely', () => {
    const setupResult = setup();
    act(() => {
      setupResult.result.current.joinRoom({
        roomCode: 'ABCDEFGH',
        nickname: 'Mara',
      });
    });
    const socket = setupResult.sockets[0]!;

    act(() => {
      socket.message({ type: 'state', snapshot: openRoomSnapshot() });
    });
    expect(setupResult.store.getState().lastConnectionError).toMatchObject({
      code: 'invalid_server_message',
    });
    expect(socket.closed).toBe(true);

    act(() => {
      setupResult.result.current.joinRoom({
        roomCode: 'ABCDEFGH',
        nickname: 'Mara',
      });
    });
    const errorSocket = setupResult.sockets[1]!;
    act(() => {
      errorSocket.message({
        type: 'connection_error',
        code: 'wrong_room_password',
        message: 'private server text',
      });
    });
    expect(setupResult.result.current.entryError).toMatchObject({
      field: 'password',
      message: 'The room password was not accepted.',
    });
  });

  it('cleans up only the current socket on unmount', () => {
    const setupResult = setup();
    act(() => {
      setupResult.result.current.joinRoom({
        roomCode: 'ABCDEFGH',
        nickname: 'Mara',
      });
    });
    const socket = setupResult.sockets[0]!;

    setupResult.unmount();

    expect(socket.closed).toBe(true);
  });
});
