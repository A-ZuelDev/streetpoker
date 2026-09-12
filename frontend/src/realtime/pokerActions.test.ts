import { describe, expect, it } from 'vitest';

import { activeRoomSnapshot } from '../test/roomSnapshots';
import {
  buildPokerCommand,
  pokerActionContextKey,
  pokerCommandSchema,
  safePokerCommandMessage,
  totalToIsServerDescribed,
  wagerBoundsAreConsistent,
  type PokerCommandType,
} from './pokerActions';

const common = {
  command_id: ' action-1 ',
  hand_number: 1,
  expected_action_sequence: 0,
};

describe('pokerCommandSchema', () => {
  it.each([
    ['fold', { type: 'fold', ...common }],
    ['check', { type: 'check', ...common }],
    ['call', { type: 'call', ...common }],
    ['bet_to', { type: 'bet_to', ...common, total: 200 }],
    ['raise_to', { type: 'raise_to', ...common, total: 400 }],
  ])('accepts the strict %s command', (_label, command) => {
    expect(pokerCommandSchema.parse(command)).toMatchObject({
      ...command,
      command_id: 'action-1',
    });
  });

  it('accepts action sequence zero', () => {
    expect(
      pokerCommandSchema.safeParse({ type: 'fold', ...common }).success,
    ).toBe(true);
  });

  it.each([
    ['extra field', { type: 'fold', ...common, extra: true }],
    ['missing field', { type: 'fold', command_id: 'action-1', hand_number: 1 }],
    ['empty command ID', { type: 'fold', ...common, command_id: '   ' }],
    [
      'long command ID',
      { type: 'fold', ...common, command_id: 'x'.repeat(65) },
    ],
    ['zero hand', { type: 'fold', ...common, hand_number: 0 }],
    ['negative hand', { type: 'fold', ...common, hand_number: -1 }],
    [
      'negative sequence',
      { type: 'fold', ...common, expected_action_sequence: -1 },
    ],
    ['zero total', { type: 'bet_to', ...common, total: 0 }],
    ['negative total', { type: 'raise_to', ...common, total: -1 }],
    ['boolean', { type: 'bet_to', ...common, total: true }],
    ['fraction', { type: 'bet_to', ...common, total: 1.5 }],
    ['NaN', { type: 'bet_to', ...common, total: Number.NaN }],
    [
      'infinity',
      { type: 'bet_to', ...common, total: Number.POSITIVE_INFINITY },
    ],
    [
      'unsafe integer',
      { type: 'bet_to', ...common, total: Number.MAX_SAFE_INTEGER + 1 },
    ],
  ])('rejects %s', (_label, command) => {
    expect(pokerCommandSchema.safeParse(command).success).toBe(false);
  });
});

describe('authoritative poker command construction', () => {
  it.each([
    ['fold', undefined],
    ['call', undefined],
    ['raise_to', 500],
  ] as const)('uses current server context for %s', (type, totalTo) => {
    const snapshot = activeRoomSnapshot();
    const hand = snapshot.active_hand!;
    const request =
      totalTo === undefined
        ? { type, contextKey: pokerActionContextKey(hand, type) }
        : { type, contextKey: pokerActionContextKey(hand, type), totalTo };

    expect(
      buildPokerCommand({
        snapshot,
        guestId: 'guest_host',
        request,
        commandId: 'command-id',
      }),
    ).toEqual({
      type,
      command_id: 'command-id',
      hand_number: 1,
      expected_action_sequence: 2,
      ...(totalTo === undefined ? {} : { total: totalTo }),
    });
  });

  it('rejects stale context, absent kinds, and actor mismatch', () => {
    const snapshot = activeRoomSnapshot();
    const hand = snapshot.active_hand!;
    const request = {
      type: 'call' as const,
      contextKey: pokerActionContextKey(hand, 'call'),
    };
    expect(
      buildPokerCommand({
        snapshot,
        guestId: 'guest_host',
        request: { ...request, contextKey: 'stale' },
        commandId: 'one',
      }),
    ).toBeNull();
    expect(
      buildPokerCommand({
        snapshot,
        guestId: 'guest_alice',
        request,
        commandId: 'two',
      }),
    ).toBeNull();
    hand.legal_actions.kinds = ['fold', 'raise'];
    hand.legal_actions.call = null;
    expect(
      buildPokerCommand({
        snapshot,
        guestId: 'guest_host',
        request,
        commandId: 'three',
      }),
    ).toBeNull();
  });

  it.each<PokerCommandType>(['fold', 'check', 'call', 'bet_to', 'raise_to'])(
    'includes command type %s in stable context keys',
    (type) => {
      const hand = activeRoomSnapshot().active_hand!;
      expect(pokerActionContextKey(hand, type)).toContain(`"${type}"`);
    },
  );
});

describe('server-described wager totals', () => {
  it('supports a full interval and a sole short all-in', () => {
    const full = {
      minimum_full_to: 200,
      maximum_to: 500,
      short_all_in_to: null,
    };
    const short = {
      minimum_full_to: 200,
      maximum_to: 150,
      short_all_in_to: 150,
    };
    expect(wagerBoundsAreConsistent(full)).toBe(true);
    expect(totalToIsServerDescribed(full, 200)).toBe(true);
    expect(totalToIsServerDescribed(full, 350)).toBe(true);
    expect(totalToIsServerDescribed(full, 500)).toBe(true);
    expect(totalToIsServerDescribed(full, 199)).toBe(false);
    expect(wagerBoundsAreConsistent(short)).toBe(true);
    expect(totalToIsServerDescribed(short, 150)).toBe(true);
    expect(totalToIsServerDescribed(short, 175)).toBe(false);
  });

  it.each([
    { minimum_full_to: 200, maximum_to: 150, short_all_in_to: null },
    { minimum_full_to: 200, maximum_to: 150, short_all_in_to: 149 },
    { minimum_full_to: 200, maximum_to: 250, short_all_in_to: 250 },
  ])('rejects malformed short-all-in relationships', (bounds) => {
    expect(wagerBoundsAreConsistent(bounds)).toBe(false);
    expect(totalToIsServerDescribed(bounds, bounds.maximum_to)).toBe(false);
  });
});

it('maps errors without exposing arbitrary server text', () => {
  expect(safePokerCommandMessage('stale_game_state')).toBe(
    'The table changed before your action was accepted.',
  );
  expect(safePokerCommandMessage('future_private_error')).toBe(
    'The server rejected the poker action.',
  );
});
