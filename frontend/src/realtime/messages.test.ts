import { describe, expect, it } from 'vitest';

import {
  activeRoomSnapshot,
  completedRoomSnapshot,
  openRoomSnapshot,
} from '../test/roomSnapshots';
import { connectFrameSchema, serverMessageSchema } from './messages';
import { standUpPenaltyPerRecipientMaximum } from './protocolLimits';

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

  it('accepts a frozen paused timer and rejects inconsistent pause projections', () => {
    const snapshot = activeRoomSnapshot();
    snapshot.room.is_paused = true;
    snapshot.active_hand!.action_deadline_unix_ms = null;
    snapshot.active_hand!.action_timer_remaining_ms = 12_000;
    expect(
      serverMessageSchema.safeParse({ type: 'state', snapshot }).success,
    ).toBe(true);

    snapshot.room.is_paused = false;
    expect(
      serverMessageSchema.safeParse({ type: 'state', snapshot }).success,
    ).toBe(false);
  });

  it('accepts seat-oriented Stand-Up state and rejects internal identities', () => {
    const snapshot = activeRoomSnapshot();
    snapshot.room.settings.stand_up_enabled = true;
    snapshot.room.settings.stand_up_penalty_per_recipient_chips = 25;
    snapshot.room.stand_up.active_round = {
      start_hand_number: 1,
      last_processed_hand_number: 0,
      penalty_per_recipient_chips: 25,
      participants: [
        { seat_index: 0, is_cleared: false },
        { seat_index: 1, is_cleared: true },
        { seat_index: 2, is_cleared: false },
      ],
    };
    snapshot.room.stand_up.last_result = {
      type: 'resolution',
      start_hand_number: 1,
      hand_number: 2,
      participant_seat_indexes: [0, 1, 2],
      squid_seat_index: 2,
      penalty_per_recipient_chips: 25,
      intended_total: 50,
      actual_total: 50,
      shortfall: 0,
      transfers: [
        { from_seat_index: 2, to_seat_index: 0, chips: 25 },
        { from_seat_index: 2, to_seat_index: 1, chips: 25 },
      ],
    };
    expect(
      serverMessageSchema.safeParse({ type: 'state', snapshot }).success,
    ).toBe(true);

    snapshot.room.stand_up.last_result = {
      type: 'cancellation',
      start_hand_number: 3,
      last_processed_hand_number: 4,
      participants: [
        { seat_index: 0, is_cleared: true },
        { seat_index: 2, is_cleared: false },
      ],
      reason: 'disabled',
    };
    expect(
      serverMessageSchema.safeParse({ type: 'state', snapshot }).success,
    ).toBe(true);

    const privateState = structuredClone(snapshot) as unknown as {
      room: { stand_up: { active_round: Record<string, unknown> } };
    };
    privateState.room.stand_up.active_round.player_id = 'private-player';
    expect(
      serverMessageSchema.safeParse({
        type: 'state',
        snapshot: privateState,
      }).success,
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

  it('accepts public session accounting and rejects internal ledger identities', () => {
    const snapshot = openRoomSnapshot();
    snapshot.room.session = {
      ledger_sequence: 1,
      adjustments: [
        {
          sequence: 1,
          adjustment_type: 'rebuy',
          target_nickname: 'Alice',
          target_seat_index: 1,
          delta: 500,
          resulting_stack: 10_500,
          initiated_by_host: true,
          initiator_seat_index: null,
          reason: 'Top-up',
        },
      ],
      players: [
        {
          nickname: 'Alice',
          seat_index: 1,
          current_stack: 10_500,
          starting_stack: 10_000,
          external_added: 500,
          external_removed: 0,
          poker_net: 0,
          hands_played: 0,
        },
      ],
    };
    expect(
      serverMessageSchema.safeParse({ type: 'state', snapshot }).success,
    ).toBe(true);

    const privateLedger = structuredClone(snapshot) as unknown as {
      room: { session: { adjustments: Record<string, unknown>[] } };
    };
    privateLedger.room.session.adjustments[0]!.player_id = 'private-player';
    expect(
      serverMessageSchema.safeParse({
        type: 'state',
        snapshot: privateLedger,
      }).success,
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

  it('rejects an unsafe Stand-Up penalty before replacing client state', () => {
    const snapshot = openRoomSnapshot();
    snapshot.room.settings.stand_up_penalty_per_recipient_chips =
      standUpPenaltyPerRecipientMaximum + 1;
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
