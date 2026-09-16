import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import { StrictMode, type PropsWithChildren } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { createBackendUrls } from '../api/backendUrls';
import { activeRoomSnapshot, openRoomSnapshot } from '../test/roomSnapshots';
import type { ServerMessage } from './messages';
import {
  pokerActionContextKey,
  type PokerActionRequest,
  type PokerCommand,
} from './pokerActions';
import type { RoomSocket, RoomSocketOptions } from './roomSocket';
import type { RoomCommand, RoomCommandRequest } from './roomCommands';
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
  sendFails = false;
  readonly commands: PokerCommand[] = [];
  readonly roomCommands: RoomCommand[] = [];

  constructor(readonly options: RoomSocketOptions) {}

  close() {
    this.closed = true;
  }

  sendPokerCommand(command: PokerCommand) {
    if (this.closed || this.sendFails) {
      return false;
    }
    this.commands.push(command);
    return true;
  }

  sendRoomCommand(command: RoomCommand) {
    if (this.closed || this.sendFails) {
      return false;
    }
    this.roomCommands.push(command);
    return true;
  }

  message(message: ServerMessage) {
    this.options.events.onMessage(message);
  }

  failure() {
    this.options.events.onFailure(new Error('not used') as never);
  }

  serverClose(close = { code: 1006, reason: '', wasClean: false }) {
    this.options.events.onClose(close);
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

  it.each([
    ['fold', undefined],
    ['check', undefined],
    ['call', undefined],
    ['bet_to', 350],
    ['raise_to', 500],
  ] as const)('sends one current authoritative %s command', (type, totalTo) => {
    const commandIdFactory = vi.fn(() => `id-${type}`);
    const setupResult = setup({ commandIdFactory });
    act(() => {
      setupResult.result.current.joinRoom({
        roomCode: 'ABCDEFGH',
        nickname: 'Mara',
      });
    });
    const socket = setupResult.sockets[0]!;
    const snapshot = activeRoomSnapshot();
    const hand = snapshot.active_hand!;
    if (type === 'check' || type === 'bet_to') {
      hand.legal_actions.kinds = ['fold', 'check', 'bet'];
      hand.legal_actions.call = null;
      hand.legal_actions.raise_to = null;
      hand.legal_actions.bet_to = {
        minimum_full_to: 200,
        maximum_to: 1_000,
        short_all_in_to: null,
      };
    }
    act(() => {
      socket.message({
        type: 'connected',
        guest_id: 'guest_host',
        room_code: 'ABCDEFGH',
      });
      socket.message({ type: 'state', snapshot });
    });
    const request: PokerActionRequest =
      totalTo === undefined
        ? { type, contextKey: pokerActionContextKey(hand, type) }
        : { type, contextKey: pokerActionContextKey(hand, type), totalTo };

    act(() => {
      expect(setupResult.result.current.sendPokerAction(request)).toBe(true);
      expect(setupResult.result.current.sendPokerAction(request)).toBe(false);
    });

    expect(commandIdFactory).toHaveBeenCalledOnce();
    expect(socket.commands).toEqual([
      {
        type,
        command_id: `id-${type}`,
        hand_number: 1,
        expected_action_sequence: 2,
        ...(totalTo === undefined ? {} : { total: totalTo }),
      },
    ]);
    expect(setupResult.store.getState().snapshot).toBe(snapshot);
  });

  it('retains pending on ack, clears on state advancement, and ignores late frames', () => {
    const setupResult = setup({ commandIdFactory: () => 'command-1' });
    act(() => {
      setupResult.result.current.joinRoom({
        roomCode: 'ABCDEFGH',
        nickname: 'Mara',
      });
    });
    const socket = setupResult.sockets[0]!;
    const snapshot = activeRoomSnapshot();
    act(() => {
      socket.message({
        type: 'connected',
        guest_id: 'guest_host',
        room_code: 'ABCDEFGH',
      });
      socket.message({ type: 'state', snapshot });
      setupResult.result.current.sendPokerAction({
        type: 'call',
        contextKey: pokerActionContextKey(snapshot.active_hand!, 'call'),
      });
      socket.message({ type: 'command_ack', command_id: 'command-1' });
    });
    expect(setupResult.store.getState().pendingPokerCommand?.commandId).toBe(
      'command-1',
    );

    const advanced = activeRoomSnapshot();
    advanced.active_hand!.action_sequence = 3;
    advanced.active_hand!.current_actor = 'guest_alice';
    advanced.active_hand!.legal_actions.actor = 'guest_alice';
    act(() => {
      socket.message({ type: 'state', snapshot: advanced });
      socket.message({ type: 'command_ack', command_id: 'command-1' });
      socket.message({
        type: 'command_error',
        command_id: 'command-1',
        code: 'stale_game_state',
        message: 'private server detail',
      });
    });
    expect(setupResult.store.getState().pendingPokerCommand).toBeNull();
    expect(setupResult.store.getState().snapshot).toBe(advanced);
    expect(setupResult.store.getState().lastPokerCommandError).toBeNull();
    expect(socket.commands).toHaveLength(1);
  });

  it('maps a matching error safely and rolls back a synchronous send failure', () => {
    const setupResult = setup({ commandIdFactory: () => 'command-1' });
    act(() => {
      setupResult.result.current.joinRoom({
        roomCode: 'ABCDEFGH',
        nickname: 'Mara',
      });
    });
    const socket = setupResult.sockets[0]!;
    const snapshot = activeRoomSnapshot();
    act(() => {
      socket.message({
        type: 'connected',
        guest_id: 'guest_host',
        room_code: 'ABCDEFGH',
      });
      socket.message({ type: 'state', snapshot });
      setupResult.result.current.sendPokerAction({
        type: 'call',
        contextKey: pokerActionContextKey(snapshot.active_hand!, 'call'),
      });
      socket.message({
        type: 'command_error',
        command_id: 'command-1',
        code: 'illegal_call',
        message: 'private server detail',
      });
    });
    expect(setupResult.store.getState()).toMatchObject({
      pendingPokerCommand: null,
      snapshot,
      lastPokerCommandError: {
        message: 'Calling is not available now.',
      },
    });
    expect(
      String(setupResult.store.getState().lastPokerCommandError),
    ).not.toContain('private server detail');

    socket.sendFails = true;
    act(() => {
      expect(
        setupResult.result.current.sendPokerAction({
          type: 'call',
          contextKey: pokerActionContextKey(snapshot.active_hand!, 'call'),
        }),
      ).toBe(false);
    });
    expect(setupResult.store.getState().pendingPokerCommand).toBeNull();
    expect(setupResult.store.getState().lastPokerCommandError?.code).toBe(
      'action_send_failed',
    );
    expect(setupResult.store.getState()).toMatchObject({
      status: 'connected',
      snapshot,
      lastPokerCommandError: {
        message:
          'The action could not be sent. Try again when the table is ready.',
      },
    });
    expect(
      setupResult.store.getState().lastPokerCommandError?.message,
    ).not.toContain('reconnect');
    expect(socket.commands).toHaveLength(1);
  });

  it('clears a failed room send without fabricating a disconnect', () => {
    const setupResult = setup({ commandIdFactory: () => 'room-command' });
    act(() => {
      setupResult.result.current.joinRoom({
        roomCode: 'ABCDEFGH',
        nickname: 'Mara',
      });
    });
    const socket = setupResult.sockets[0]!;
    const snapshot = openRoomSnapshot();
    act(() => {
      socket.message({
        type: 'connected',
        guest_id: 'guest_host',
        room_code: 'ABCDEFGH',
      });
      socket.message({ type: 'state', snapshot });
    });
    socket.sendFails = true;

    act(() => {
      expect(
        setupResult.result.current.sendRoomCommand({ type: 'start_hand' }),
      ).toBe(false);
    });

    expect(setupResult.store.getState()).toMatchObject({
      status: 'connected',
      snapshot,
      pendingRoomCommand: null,
      lastRoomCommandError: {
        code: 'room_command_send_failed',
        message:
          'The room action could not be sent. Try again when the table is ready.',
      },
    });
  });

  it('sends nothing while syncing, disconnected, stale, or superseded', () => {
    const setupResult = setup({ commandIdFactory: () => 'command-1' });
    act(() => {
      setupResult.result.current.joinRoom({
        roomCode: 'ABCDEFGH',
        nickname: 'Mara',
      });
    });
    const first = setupResult.sockets[0]!;
    const snapshot = activeRoomSnapshot();
    const request = {
      type: 'call' as const,
      contextKey: pokerActionContextKey(snapshot.active_hand!, 'call'),
    };
    expect(setupResult.result.current.sendPokerAction(request)).toBe(false);
    act(() => {
      first.message({
        type: 'connected',
        guest_id: 'guest_host',
        room_code: 'ABCDEFGH',
      });
    });
    expect(setupResult.result.current.sendPokerAction(request)).toBe(false);
    act(() => {
      first.message({ type: 'state', snapshot });
      first.serverClose();
    });
    expect(setupResult.result.current.sendPokerAction(request)).toBe(false);
    act(() => setupResult.result.current.reconnect());
    expect(setupResult.result.current.sendPokerAction(request)).toBe(false);
    expect(first.commands).toEqual([]);
    expect(setupResult.sockets[1]!.commands).toEqual([]);
  });

  it('rejects inconsistent action state through the protocol fail-closed path', () => {
    const setupResult = setup();
    act(() => {
      setupResult.result.current.joinRoom({
        roomCode: 'ABCDEFGH',
        nickname: 'Mara',
      });
    });
    const socket = setupResult.sockets[0]!;
    const snapshot = activeRoomSnapshot();
    snapshot.active_hand!.legal_actions.kinds.push('check');
    act(() => {
      socket.message({
        type: 'connected',
        guest_id: 'guest_host',
        room_code: 'ABCDEFGH',
      });
      socket.message({ type: 'state', snapshot });
    });
    expect(socket.closed).toBe(true);
    expect(setupResult.store.getState()).toMatchObject({
      status: 'disconnected',
      lastConnectionError: { code: 'invalid_server_message' },
    });
  });

  it('sends one exact room command and holds it through ack until state', () => {
    const setupResult = setup({ commandIdFactory: () => 'room-command' });
    act(() => {
      setupResult.result.current.joinRoom({
        roomCode: 'ABCDEFGH',
        nickname: 'Mara',
      });
    });
    const socket = setupResult.sockets[0]!;
    const snapshot = openRoomSnapshot();
    act(() => {
      socket.message({
        type: 'connected',
        guest_id: 'guest_host',
        room_code: 'ABCDEFGH',
      });
      socket.message({ type: 'state', snapshot });
      expect(
        setupResult.result.current.sendRoomCommand({
          type: 'request_seat',
          seatIndex: 0,
        }),
      ).toBe(true);
      expect(
        setupResult.result.current.sendRoomCommand({
          type: 'request_seat',
          seatIndex: 0,
        }),
      ).toBe(false);
    });
    expect(socket.roomCommands).toEqual([
      {
        type: 'request_seat',
        command_id: 'room-command',
        seat_index: 0,
      },
    ]);
    expect(setupResult.store.getState().snapshot).toBe(snapshot);
    act(() => {
      socket.message({ type: 'command_ack', command_id: 'room-command' });
    });
    expect(setupResult.store.getState().pendingRoomCommand?.acknowledged).toBe(
      true,
    );
    act(() => {
      socket.message({ type: 'state', snapshot: structuredClone(snapshot) });
    });
    expect(setupResult.store.getState().pendingRoomCommand).toBeNull();
  });

  it('uses exact next_hand_number and local room error text', () => {
    const setupResult = setup({ commandIdFactory: () => 'start-command' });
    act(() => {
      setupResult.result.current.joinRoom({
        roomCode: 'ABCDEFGH',
        nickname: 'Mara',
      });
    });
    const socket = setupResult.sockets[0]!;
    const snapshot = openRoomSnapshot();
    snapshot.next_hand_number = 9;
    act(() => {
      socket.message({
        type: 'connected',
        guest_id: 'guest_host',
        room_code: 'ABCDEFGH',
      });
      socket.message({ type: 'state', snapshot });
      setupResult.result.current.sendRoomCommand({ type: 'start_hand' });
      socket.message({
        type: 'command_error',
        command_id: 'start-command',
        code: 'insufficient_players',
        message: 'private server player detail',
      });
    });
    expect(socket.roomCommands).toEqual([
      {
        type: 'start_hand',
        command_id: 'start-command',
        hand_number: 9,
      },
    ]);
    expect(setupResult.store.getState().lastRoomCommandError?.message).toBe(
      'At least two eligible seated players are required.',
    );
    expect(JSON.stringify(setupResult.store.getState())).not.toContain(
      'private server player detail',
    );
  });

  it('sends the remaining room command families with exact wire fields', () => {
    const requestedSnapshot = () => {
      const snapshot = openRoomSnapshot();
      snapshot.room.seat_requests = [
        {
          guest_id: 'guest_alice',
          nickname: 'Alice',
          seat_index: 0,
        },
      ];
      return snapshot;
    };
    const cases: readonly {
      guestId: string;
      snapshot: ReturnType<typeof openRoomSnapshot>;
      request: RoomCommandRequest;
      expected: RoomCommand;
    }[] = [
      {
        guestId: 'guest_host',
        snapshot: requestedSnapshot(),
        request: { type: 'approve_seat', targetGuestId: 'guest_alice' },
        expected: {
          type: 'approve_seat',
          command_id: 'room-command',
          target_guest_id: 'guest_alice',
        },
      },
      {
        guestId: 'guest_host',
        snapshot: requestedSnapshot(),
        request: { type: 'reject_seat', targetGuestId: 'guest_alice' },
        expected: {
          type: 'reject_seat',
          command_id: 'room-command',
          target_guest_id: 'guest_alice',
        },
      },
      {
        guestId: 'guest_alice',
        snapshot: openRoomSnapshot(),
        request: { type: 'stand' },
        expected: { type: 'stand', command_id: 'room-command' },
      },
      {
        guestId: 'guest_host',
        snapshot: openRoomSnapshot(),
        request: { type: 'kick', targetGuestId: 'guest_alice' },
        expected: {
          type: 'kick',
          command_id: 'room-command',
          target_guest_id: 'guest_alice',
        },
      },
      {
        guestId: 'guest_host',
        snapshot: openRoomSnapshot(),
        request: {
          type: 'update_settings',
          patch: { room_name: 'Saturday Night', password: null },
        },
        expected: {
          type: 'update_settings',
          command_id: 'room-command',
          room_name: 'Saturday Night',
          password: null,
        },
      },
      {
        guestId: 'guest_host',
        snapshot: openRoomSnapshot(),
        request: { type: 'close_room' },
        expected: { type: 'close_room', command_id: 'room-command' },
      },
    ];

    for (const commandCase of cases) {
      const setupResult = setup({ commandIdFactory: () => 'room-command' });
      act(() => {
        setupResult.result.current.joinRoom({
          roomCode: 'ABCDEFGH',
          nickname: 'Mara',
        });
      });
      const socket = setupResult.sockets[0]!;
      act(() => {
        socket.message({
          type: 'connected',
          guest_id: commandCase.guestId,
          room_code: 'ABCDEFGH',
        });
        socket.message({ type: 'state', snapshot: commandCase.snapshot });
        expect(
          setupResult.result.current.sendRoomCommand(commandCase.request),
        ).toBe(true);
      });
      expect(socket.roomCommands).toEqual([commandCase.expected]);
      setupResult.unmount();
    }
  });

  it('ends leave on matching ack and ignores later frames', () => {
    const setupResult = setup({ commandIdFactory: () => 'leave-command' });
    act(() => {
      setupResult.result.current.joinRoom({
        roomCode: 'ABCDEFGH',
        nickname: 'Alice',
      });
    });
    const socket = setupResult.sockets[0]!;
    const snapshot = openRoomSnapshot();
    act(() => {
      socket.message({
        type: 'connected',
        guest_id: 'guest_alice',
        room_code: 'ABCDEFGH',
      });
      socket.message({ type: 'state', snapshot });
      setupResult.result.current.sendRoomCommand({ type: 'leave' });
      socket.message({ type: 'command_ack', command_id: 'leave-command' });
      socket.message({ type: 'state', snapshot: activeRoomSnapshot() });
    });
    expect(setupResult.store.getState()).toMatchObject({
      status: 'idle',
      snapshot: null,
      guestId: null,
      roomExit: { kind: 'left' },
    });
    expect(setupResult.result.current.canReconnect).toBe(false);
    expect(setupResult.result.current.sendRoomCommand({ type: 'leave' })).toBe(
      false,
    );
  });

  it('classifies only exact terminal closes and preserves reconnect otherwise', () => {
    const kicked = setup();
    act(() => {
      kicked.result.current.joinRoom({
        roomCode: 'ABCDEFGH',
        nickname: 'Alice',
      });
    });
    const kickedSocket = kicked.sockets[0]!;
    act(() => {
      kickedSocket.message({
        type: 'connected',
        guest_id: 'guest_alice',
        room_code: 'ABCDEFGH',
      });
      kickedSocket.message({ type: 'state', snapshot: openRoomSnapshot() });
      kickedSocket.serverClose({
        code: 1008,
        reason: 'removed from room',
        wasClean: true,
      });
    });
    expect(kicked.store.getState().roomExit?.kind).toBe('kicked');
    expect(kicked.result.current.canReconnect).toBe(false);

    const unknown = setup();
    act(() => {
      unknown.result.current.joinRoom({
        roomCode: 'ABCDEFGH',
        nickname: 'Mara',
      });
    });
    const unknownSocket = unknown.sockets[0]!;
    act(() => {
      unknownSocket.message({
        type: 'connected',
        guest_id: 'guest_host',
        room_code: 'ABCDEFGH',
      });
      unknownSocket.message({ type: 'state', snapshot: openRoomSnapshot() });
      unknownSocket.serverClose({
        code: 1008,
        reason: 'removed from room later',
        wasClean: true,
      });
    });
    expect(unknown.store.getState().roomExit).toBeNull();
    expect(unknown.store.getState().status).toBe('disconnected');
    expect(unknown.result.current.canReconnect).toBe(true);
  });

  it.each([
    ['room_closed', 'closed'],
    ['membership_required', 'kicked'],
  ] as const)(
    'ends a stale confirmed session when reconnect receives %s',
    (code, expectedExit) => {
      const setupResult = setup();
      act(() => {
        setupResult.result.current.joinRoom({
          roomCode: 'ABCDEFGH',
          nickname: 'Mara',
        });
      });
      const first = setupResult.sockets[0]!;
      act(() => {
        first.message({
          type: 'connected',
          guest_id: 'guest_host',
          room_code: 'ABCDEFGH',
        });
        first.message({ type: 'state', snapshot: openRoomSnapshot() });
        first.serverClose();
        setupResult.result.current.reconnect();
      });
      const reconnect = setupResult.sockets[1]!;
      act(() => {
        reconnect.message({
          type: 'connection_error',
          code,
          message: 'private terminal detail',
        });
      });
      expect(setupResult.store.getState()).toMatchObject({
        status: 'idle',
        snapshot: null,
        guestId: null,
        roomExit: { kind: expectedExit },
      });
      expect(setupResult.result.current.canReconnect).toBe(false);
      expect(JSON.stringify(setupResult.store.getState())).not.toContain(
        'private terminal detail',
      );
    },
  );

  it('ends on an authoritative closed state before the later close frame', () => {
    const setupResult = setup();
    act(() => {
      setupResult.result.current.joinRoom({
        roomCode: 'ABCDEFGH',
        nickname: 'Mara',
      });
    });
    const socket = setupResult.sockets[0]!;
    const closed = openRoomSnapshot();
    closed.room.status = 'closed';
    act(() => {
      socket.message({
        type: 'connected',
        guest_id: 'guest_host',
        room_code: 'ABCDEFGH',
      });
      socket.message({ type: 'state', snapshot: openRoomSnapshot() });
      socket.message({ type: 'state', snapshot: closed });
      socket.serverClose({ code: 1000, reason: 'room closed', wasClean: true });
    });
    expect(setupResult.store.getState().roomExit).toEqual({
      kind: 'closed',
      roomCode: 'ABCDEFGH',
      roomName: 'Friday Night',
    });
    expect(setupResult.result.current.canReconnect).toBe(false);
  });
});
