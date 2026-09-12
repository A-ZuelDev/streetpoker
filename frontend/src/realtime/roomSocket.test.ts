import { describe, expect, it, vi } from 'vitest';

import { createBackendUrls } from '../api/backendUrls';
import { openRoomSnapshot } from '../test/roomSnapshots';
import { RoomSocket } from './roomSocket';

class FakeWebSocket extends EventTarget {
  readyState: number = WebSocket.CONNECTING;
  readonly sent: string[] = [];
  readonly closes: Array<{
    code: number | undefined;
    reason: string | undefined;
  }> = [];

  send(value: string) {
    this.sent.push(value);
  }

  close(code?: number, reason?: string) {
    this.closes.push({ code, reason });
    this.readyState = WebSocket.CLOSED;
  }

  open() {
    this.readyState = WebSocket.OPEN;
    this.dispatchEvent(new Event('open'));
  }

  message(data: unknown) {
    this.dispatchEvent(new MessageEvent('message', { data }));
  }

  serverClose() {
    this.readyState = WebSocket.CLOSED;
    this.dispatchEvent(new CloseEvent('close'));
  }
}

function setup() {
  const fake = new FakeWebSocket();
  const events = {
    onMessage: vi.fn(),
    onFailure: vi.fn(),
    onClose: vi.fn(),
  };
  const factory = vi.fn(() => fake as unknown as WebSocket);
  const token = 'A'.repeat(43);
  const roomSocket = new RoomSocket({
    urls: createBackendUrls('https://example.test/api/'),
    roomCode: 'abcdefgh',
    connectFrame: {
      type: 'connect',
      guest_token: token,
      nickname: 'Mara',
      password: 'private-password',
    },
    events,
    webSocketFactory: factory,
  });
  return { fake, events, factory, roomSocket, token };
}

describe('RoomSocket', () => {
  it('keeps credentials out of the URL and sends connect once after open', () => {
    const { fake, factory, token } = setup();

    expect(factory).toHaveBeenCalledWith(
      'wss://example.test/api/ws/rooms/ABCDEFGH',
    );
    expect(String(factory.mock.calls[0])).not.toContain(token);
    expect(String(factory.mock.calls[0])).not.toContain('private-password');
    expect(fake.sent).toEqual([]);

    fake.open();
    fake.dispatchEvent(new Event('open'));

    expect(fake.sent).toHaveLength(1);
    expect(JSON.parse(fake.sent[0]!)).toEqual({
      type: 'connect',
      guest_token: token,
      nickname: 'Mara',
      password: 'private-password',
    });
  });

  it('emits strictly parsed server messages', () => {
    const { fake, events } = setup();
    fake.open();
    const message = { type: 'state', snapshot: openRoomSnapshot() };

    fake.message(JSON.stringify(message));

    expect(events.onMessage).toHaveBeenCalledWith(message);
    expect(events.onFailure).not.toHaveBeenCalled();
  });

  it.each([
    ['malformed JSON', '{'],
    ['invalid schema', JSON.stringify({ type: 'connected', extra: true })],
    ['unknown schema', JSON.stringify({ type: 'future' })],
  ])('rejects %s without exposing its contents', (_label, data) => {
    const { fake, events } = setup();
    fake.open();

    fake.message(data);

    expect(events.onFailure).toHaveBeenCalledWith(
      expect.objectContaining({ code: 'invalid_server_message' }),
    );
    expect(fake.closes).toEqual([
      { code: 1002, reason: 'invalid server message' },
    ]);
  });

  it('rejects binary messages', () => {
    const { fake, events } = setup();
    fake.open();

    fake.message(new Blob(['private frame']));

    expect(events.onFailure).toHaveBeenCalledWith(
      expect.objectContaining({ code: 'invalid_server_message' }),
    );
    expect(events.onFailure.mock.calls.join(' ')).not.toContain(
      'private frame',
    );
  });

  it('reports transport failure and normal close through typed events', () => {
    const first = setup();
    first.fake.dispatchEvent(new Event('error'));
    expect(first.events.onFailure).toHaveBeenCalledWith(
      expect.objectContaining({ code: 'socket_error' }),
    );

    const second = setup();
    second.fake.serverClose();
    expect(second.events.onClose).toHaveBeenCalledOnce();
  });

  it('closes idempotently and never reconnects', () => {
    const { fake, factory, roomSocket } = setup();

    roomSocket.close();
    roomSocket.close();

    expect(fake.closes).toHaveLength(1);
    expect(factory).toHaveBeenCalledOnce();
  });

  it('sends one strict poker command only while open', () => {
    const { fake, roomSocket } = setup();
    const command = {
      type: 'check' as const,
      command_id: 'command-1',
      hand_number: 1,
      expected_action_sequence: 2,
    };

    expect(roomSocket.sendPokerCommand(command)).toBe(false);
    fake.open();
    expect(roomSocket.sendPokerCommand(command)).toBe(true);
    expect(fake.sent.map((value) => JSON.parse(value))).toEqual([
      {
        type: 'connect',
        guest_token: 'A'.repeat(43),
        nickname: 'Mara',
        password: 'private-password',
      },
      command,
    ]);
    roomSocket.close();
    expect(roomSocket.sendPokerCommand(command)).toBe(false);
    expect(fake.sent).toHaveLength(2);
  });

  it('does not send or queue an invalid poker command', () => {
    const { fake, roomSocket } = setup();
    fake.open();
    expect(
      roomSocket.sendPokerCommand({
        type: 'bet_to',
        command_id: 'bad',
        hand_number: 1,
        expected_action_sequence: 2,
        total: 0,
      } as never),
    ).toBe(false);
    expect(fake.sent).toHaveLength(1);
  });

  it('returns false on synchronous send failure without retrying', () => {
    const { fake, roomSocket } = setup();
    fake.open();
    const send = vi.spyOn(fake, 'send').mockImplementation(() => {
      throw new DOMException('closed');
    });
    expect(
      roomSocket.sendPokerCommand({
        type: 'fold',
        command_id: 'one',
        hand_number: 1,
        expected_action_sequence: 2,
      }),
    ).toBe(false);
    expect(send).toHaveBeenCalledOnce();
  });
});
