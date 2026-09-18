import { describe, expect, it } from 'vitest';

import {
  activeRoomSnapshot,
  completedRoomSnapshot,
  openRoomSnapshot,
} from '../test/roomSnapshots';
import { connectFrameSchema, serverMessageSchema } from './messages';

describe('serverMessageSchema', () => {
  it.each([
    { type: 'connected', guest_id: 'guest_host', room_code: 'ABCDEFGH' },
    { type: 'command_ack', command_id: 'command-1' },
    {
      type: 'command_error',
      command_id: null,
      code: 'validation_error',
      message: 'safe',
    },
    { type: 'connection_error', code: 'room_not_found', message: 'safe' },
  ])('accepts the $type message family', (message) => {
    expect(serverMessageSchema.parse(message)).toEqual(message);
  });

  it.each([openRoomSnapshot(), activeRoomSnapshot(), completedRoomSnapshot()])(
    'accepts an exact viewer snapshot',
    (snapshot) => {
      expect(serverMessageSchema.parse({ type: 'state', snapshot })).toEqual({
        type: 'state',
        snapshot,
      });
    },
  );

  it('requires public deadline and timebank fields without internal timer state', () => {
    const snapshot = activeRoomSnapshot();
    expect(snapshot.active_hand?.action_deadline_unix_ms).toBe(
      1_800_000_000_000,
    );
    snapshot.active_hand!.action_deadline_unix_ms = 1.5;
    expect(
      serverMessageSchema.safeParse({ type: 'state', snapshot }).success,
    ).toBe(false);
    snapshot.active_hand!.action_deadline_unix_ms = 1_800_000_000_000;
    snapshot.active_hand!.current_actor_timebank_ms = 1.5;
    expect(
      serverMessageSchema.safeParse({ type: 'state', snapshot }).success,
    ).toBe(false);
    snapshot.active_hand!.current_actor_timebank_ms = 60_000;
    snapshot.active_hand!.current_actor_timebank_ms = -1;
    expect(
      serverMessageSchema.safeParse({ type: 'state', snapshot }).success,
    ).toBe(false);
    snapshot.active_hand!.current_actor_timebank_ms = 60_000;
    const extra = structuredClone(snapshot) as unknown as Record<
      string,
      unknown
    >;
    const active = extra.active_hand as Record<string, unknown>;
    active.timer_revision = 3;
    expect(
      serverMessageSchema.safeParse({ type: 'state', snapshot: extra }).success,
    ).toBe(false);
    delete active.timer_revision;
    delete active.current_actor_using_timebank;
    expect(
      serverMessageSchema.safeParse({ type: 'state', snapshot: extra }).success,
    ).toBe(false);
    active.current_actor_using_timebank = false;
    delete active.action_deadline_unix_ms;
    expect(
      serverMessageSchema.safeParse({ type: 'state', snapshot: extra }).success,
    ).toBe(false);
  });

  it('rejects unexpected nested fields', () => {
    const snapshot = openRoomSnapshot() as unknown as Record<string, unknown>;
    const room = snapshot.room as Record<string, unknown>;
    room.private_data = 'not allowed';

    expect(
      serverMessageSchema.safeParse({ type: 'state', snapshot }).success,
    ).toBe(false);
  });

  it.each([
    1.5,
    Number.NaN,
    Number.POSITIVE_INFINITY,
    Number.MAX_SAFE_INTEGER + 1,
  ])('rejects unsafe integer %s', (value) => {
    const snapshot = activeRoomSnapshot();
    snapshot.active_hand!.pot_chips = value;
    expect(
      serverMessageSchema.safeParse({ type: 'state', snapshot }).success,
    ).toBe(false);
  });

  it('rejects malformed cards and unknown messages', () => {
    const snapshot = activeRoomSnapshot();
    snapshot.active_hand!.board[0] = {
      rank: 'ace',
      suit: 'clubs',
      privateMark: true,
    } as never;

    expect(
      serverMessageSchema.safeParse({ type: 'state', snapshot }).success,
    ).toBe(false);
    expect(serverMessageSchema.safeParse({ type: 'future' }).success).toBe(
      false,
    );
  });
});

describe('connectFrameSchema', () => {
  const token = 'A'.repeat(43);

  it('accepts only the Phase 10B connect frame', () => {
    expect(
      connectFrameSchema.parse({
        type: 'connect',
        guest_token: token,
        nickname: 'Mara',
      }),
    ).toEqual({ type: 'connect', guest_token: token, nickname: 'Mara' });
  });

  it('rejects extras and malformed tokens', () => {
    expect(
      connectFrameSchema.safeParse({
        type: 'connect',
        guest_token: 'bad',
        command_id: 'not-allowed',
      }).success,
    ).toBe(false);
  });
});
