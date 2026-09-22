import { z } from 'zod';

import type { RoomView } from './messages';

const safeIntegerSchema = z
  .number()
  .refine(Number.isSafeInteger, 'Expected a safe integer.');
const positiveSafeIntegerSchema = safeIntegerSchema.refine(
  (value) => value >= 1,
  'Expected a positive safe integer.',
);
const nonnegativeSafeIntegerSchema = safeIntegerSchema.refine(
  (value) => value >= 0,
  'Expected a nonnegative safe integer.',
);
const commandIdSchema = z.string().trim().min(1).max(64);

const gameplayFields = {
  command_id: commandIdSchema,
  hand_number: positiveSafeIntegerSchema,
  expected_action_sequence: nonnegativeSafeIntegerSchema,
};

export const foldCommandSchema = z.strictObject({
  type: z.literal('fold'),
  ...gameplayFields,
});

export const checkCommandSchema = z.strictObject({
  type: z.literal('check'),
  ...gameplayFields,
});

export const callCommandSchema = z.strictObject({
  type: z.literal('call'),
  ...gameplayFields,
});

export const betToCommandSchema = z.strictObject({
  type: z.literal('bet_to'),
  ...gameplayFields,
  total: positiveSafeIntegerSchema,
});

export const raiseToCommandSchema = z.strictObject({
  type: z.literal('raise_to'),
  ...gameplayFields,
  total: positiveSafeIntegerSchema,
});

export const pokerCommandSchema = z.discriminatedUnion('type', [
  foldCommandSchema,
  checkCommandSchema,
  callCommandSchema,
  betToCommandSchema,
  raiseToCommandSchema,
]);

export type PokerCommand = z.infer<typeof pokerCommandSchema>;
export type PokerCommandType = PokerCommand['type'];
export type PokerServerKind = 'fold' | 'check' | 'call' | 'bet' | 'raise';

export interface PendingPokerCommand {
  readonly commandId: string;
  readonly type: PokerCommandType;
  readonly handNumber: number;
  readonly expectedActionSequence: number;
  readonly actorGuestId: string;
}

export type PokerActionRequest =
  | {
      readonly type: 'fold' | 'check' | 'call';
      readonly contextKey: string;
    }
  | {
      readonly type: 'bet_to' | 'raise_to';
      readonly contextKey: string;
      readonly totalTo: number;
    };

export type ActiveHand = NonNullable<RoomView['active_hand']>;
export type WagerBounds = NonNullable<ActiveHand['legal_actions']['bet_to']>;

const serverKindByCommand: Record<PokerCommandType, PokerServerKind> = {
  fold: 'fold',
  check: 'check',
  call: 'call',
  bet_to: 'bet',
  raise_to: 'raise',
};

function isPositiveSafeInteger(value: number): boolean {
  return Number.isSafeInteger(value) && value >= 1;
}

export function wagerBoundsAreConsistent(bounds: WagerBounds): boolean {
  const { minimum_full_to, maximum_to, short_all_in_to } = bounds;
  if (
    !isPositiveSafeInteger(minimum_full_to) ||
    !isPositiveSafeInteger(maximum_to)
  ) {
    return false;
  }
  if (short_all_in_to === null) {
    return minimum_full_to <= maximum_to;
  }
  return (
    isPositiveSafeInteger(short_all_in_to) &&
    maximum_to < minimum_full_to &&
    short_all_in_to === maximum_to
  );
}

export function legalActionFactsAreConsistent(hand: ActiveHand): boolean {
  const legal = hand.legal_actions;
  const kinds = new Set(legal.kinds);
  const hasCheck = kinds.has('check');
  const hasCall = kinds.has('call');
  if (
    !Number.isSafeInteger(hand.hand_number) ||
    hand.hand_number < 1 ||
    !Number.isSafeInteger(hand.action_sequence) ||
    hand.action_sequence < 0 ||
    hand.current_actor.length === 0 ||
    legal.actor.length === 0 ||
    kinds.size !== legal.kinds.length ||
    hand.current_actor !== legal.actor ||
    hasCheck === hasCall ||
    (kinds.has('bet') && kinds.has('raise')) ||
    hasCall !== (legal.call !== null) ||
    kinds.has('bet') !== (legal.bet_to !== null) ||
    kinds.has('raise') !== (legal.raise_to !== null)
  ) {
    return false;
  }
  if (
    legal.call !== null &&
    (!isPositiveSafeInteger(legal.call.chips) ||
      !isPositiveSafeInteger(legal.call.total))
  ) {
    return false;
  }
  return (
    (legal.bet_to === null || wagerBoundsAreConsistent(legal.bet_to)) &&
    (legal.raise_to === null || wagerBoundsAreConsistent(legal.raise_to))
  );
}

export function wagerBoundsForCommand(
  hand: ActiveHand,
  type: 'bet_to' | 'raise_to',
): WagerBounds | null {
  return type === 'bet_to'
    ? hand.legal_actions.bet_to
    : hand.legal_actions.raise_to;
}

export function pokerActionContextKey(
  hand: ActiveHand,
  type: PokerCommandType,
): string {
  const bounds =
    type === 'bet_to' || type === 'raise_to'
      ? wagerBoundsForCommand(hand, type)
      : null;
  return JSON.stringify([
    hand.hand_number,
    hand.action_sequence,
    hand.current_actor,
    hand.legal_actions.actor,
    type,
    bounds?.minimum_full_to ?? null,
    bounds?.maximum_to ?? null,
    bounds?.short_all_in_to ?? null,
  ]);
}

export function totalToIsServerDescribed(
  bounds: WagerBounds,
  totalTo: number,
): boolean {
  if (!isPositiveSafeInteger(totalTo) || !wagerBoundsAreConsistent(bounds)) {
    return false;
  }
  if (bounds.short_all_in_to !== null) {
    return totalTo === bounds.short_all_in_to;
  }
  return totalTo >= bounds.minimum_full_to && totalTo <= bounds.maximum_to;
}

export function buildPokerCommand(options: {
  readonly snapshot: RoomView;
  readonly guestId: string;
  readonly request: PokerActionRequest;
  readonly commandId: string;
}): PokerCommand | null {
  const { snapshot, guestId, request, commandId } = options;
  const hand = snapshot.active_hand;
  if (
    hand === null ||
    snapshot.room.is_paused ||
    guestId !== hand.current_actor ||
    guestId !== hand.legal_actions.actor ||
    !legalActionFactsAreConsistent(hand) ||
    !hand.legal_actions.kinds.includes(serverKindByCommand[request.type]) ||
    request.contextKey !== pokerActionContextKey(hand, request.type)
  ) {
    return null;
  }

  const common = {
    type: request.type,
    command_id: commandId,
    hand_number: hand.hand_number,
    expected_action_sequence: hand.action_sequence,
  } as const;

  if (request.type === 'bet_to' || request.type === 'raise_to') {
    const bounds = wagerBoundsForCommand(hand, request.type);
    if (bounds === null || !totalToIsServerDescribed(bounds, request.totalTo)) {
      return null;
    }
    return pokerCommandSchema.parse({ ...common, total: request.totalTo });
  }
  return pokerCommandSchema.parse(common);
}

const safeCommandMessages: Record<string, string> = {
  game_paused: 'The game is paused by the room host.',
  stale_game_state: 'The table changed before your action was accepted.',
  no_active_hand: 'That hand is no longer active.',
  not_current_actor: 'It is no longer your turn.',
  not_hand_participant: 'You are no longer participating in this hand.',
  not_room_member: 'You are no longer a member of this room.',
  room_not_found: 'The room is no longer available.',
  illegal_check: 'Checking is not available now.',
  illegal_call: 'Calling is not available now.',
  illegal_bet: 'Betting is not available now.',
  illegal_raise: 'Raising is not available now.',
  raise_not_reopened: 'Raising is not available now.',
  invalid_wager: 'Choose a valid wager total.',
  wager_below_minimum: 'That wager is below the server minimum.',
  wager_exceeds_stack: 'That wager is above the server maximum.',
  illegal_action: 'That action is not available now.',
  validation_error: 'The action command was rejected.',
  internal_error: 'The server could not complete the action.',
};

export function safePokerCommandMessage(code: string): string {
  return safeCommandMessages[code] ?? 'The server rejected the poker action.';
}
