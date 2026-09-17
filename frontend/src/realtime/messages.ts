import { z } from 'zod';

const safeIntegerSchema = z
  .number()
  .refine(Number.isSafeInteger, 'Expected a safe integer.');
const seatIndexSchema = safeIntegerSchema.refine(
  (value) => value >= 0 && value <= 5,
  'Expected a six-max seat index.',
);
const roomCodeSchema = z
  .string()
  .regex(/^[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{8}$/);

export const connectFrameSchema = z.strictObject({
  type: z.literal('connect'),
  guest_token: z.string().regex(/^[A-Za-z0-9_-]{43}$/),
  nickname: z.string().max(128).optional(),
  password: z.string().max(128).optional(),
});

const cardSchema = z.strictObject({
  rank: z.enum([
    'two',
    'three',
    'four',
    'five',
    'six',
    'seven',
    'eight',
    'nine',
    'ten',
    'jack',
    'queen',
    'king',
    'ace',
  ]),
  suit: z.enum(['clubs', 'diamonds', 'hearts', 'spades']),
});

const callSchema = z.strictObject({
  chips: safeIntegerSchema,
  total: safeIntegerSchema,
  is_all_in: z.boolean(),
});

const wagerBoundsSchema = z.strictObject({
  minimum_full_to: safeIntegerSchema,
  maximum_to: safeIntegerSchema,
  short_all_in_to: safeIntegerSchema.nullable(),
});

const legalActionsSchema = z.strictObject({
  actor: z.string(),
  kinds: z.array(z.enum(['check', 'call', 'bet', 'raise', 'fold'])),
  amount_to_call: safeIntegerSchema,
  call: callSchema.nullable(),
  bet_to: wagerBoundsSchema.nullable(),
  raise_to: wagerBoundsSchema.nullable(),
  raise_reopened: z.boolean(),
});

const activeHandPlayerSchema = z.strictObject({
  guest_id: z.string(),
  nickname: z.string(),
  seat_index: seatIndexSchema,
  status: z.enum(['active', 'folded', 'all_in']),
  current_stack: safeIntegerSchema,
  gross_committed: safeIntegerSchema,
  street_committed: safeIntegerSchema,
  hole_cards: z.array(cardSchema).nullable(),
});

const handPhaseSchema = z.enum([
  'preflop',
  'flop',
  'turn',
  'river',
  'showdown_ready',
  'complete_by_fold',
]);

const activeHandSchema = z.strictObject({
  hand_number: safeIntegerSchema,
  action_sequence: safeIntegerSchema,
  action_deadline_unix_ms: safeIntegerSchema,
  phase: handPhaseSchema,
  button_seat: seatIndexSchema,
  small_blind_seat: seatIndexSchema,
  big_blind_seat: seatIndexSchema,
  board: z.array(cardSchema),
  pot_chips: safeIntegerSchema,
  players: z.array(activeHandPlayerSchema),
  current_actor: z.string(),
  legal_actions: legalActionsSchema,
});

const winnerShareSchema = z.strictObject({
  guest_id: z.string(),
  chips: safeIntegerSchema,
  receives_odd_chip: z.boolean(),
});

const completedPotSchema = z.strictObject({
  pot_index: safeIntegerSchema,
  amount: safeIntegerSchema,
  winners: z.array(winnerShareSchema),
});

const completedHandPlayerSchema = z.strictObject({
  guest_id: z.string(),
  nickname: z.string(),
  seat_index: seatIndexSchema,
  folded: z.boolean(),
  final_stack: safeIntegerSchema,
  total_award: safeIntegerSchema,
  gross_committed: safeIntegerSchema,
  returned_excess: safeIntegerSchema,
  hole_cards: z.array(cardSchema).nullable(),
});

const completedHandSchema = z.strictObject({
  hand_number: safeIntegerSchema,
  final_action_sequence: safeIntegerSchema,
  phase: handPhaseSchema,
  source: z.enum(['showdown_ready', 'complete_by_fold']),
  button_seat: seatIndexSchema,
  board: z.array(cardSchema),
  players: z.array(completedHandPlayerSchema),
  pots: z.array(completedPotSchema),
});

const roomSettingsSchema = z.strictObject({
  room_name: z.string(),
  small_blind: safeIntegerSchema,
  big_blind: safeIntegerSchema,
  default_starting_stack: safeIntegerSchema,
  seating_approval_required: z.boolean(),
  max_seats: safeIntegerSchema,
  password_protected: z.boolean(),
});

const memberSchema = z.strictObject({
  guest_id: z.string(),
  nickname: z.string(),
  status: z.enum(['in_room', 'seated']),
  is_host: z.boolean(),
  stack: safeIntegerSchema.nullable(),
});

const seatSchema = z.strictObject({
  seat_index: seatIndexSchema,
  guest_id: z.string().nullable(),
  nickname: z.string().nullable(),
  stack: safeIntegerSchema.nullable(),
});

const seatRequestSchema = z.strictObject({
  guest_id: z.string(),
  nickname: z.string(),
  seat_index: seatIndexSchema,
});

const roomSchema = z.strictObject({
  room_id: z.string(),
  room_code: roomCodeSchema,
  status: z.enum(['open', 'hand_in_progress', 'closed']),
  host_guest_id: z.string(),
  settings: roomSettingsSchema,
  members: z.array(memberSchema),
  seats: z.array(seatSchema),
  seat_requests: z.array(seatRequestSchema),
});

export const roomViewSchema = z.strictObject({
  room: roomSchema,
  next_hand_number: safeIntegerSchema,
  active_hand: activeHandSchema.nullable(),
  last_hand: completedHandSchema.nullable(),
});

const connectedMessageSchema = z.strictObject({
  type: z.literal('connected'),
  guest_id: z.string(),
  room_code: roomCodeSchema,
});

const commandAckMessageSchema = z.strictObject({
  type: z.literal('command_ack'),
  command_id: z.string(),
});

const stateMessageSchema = z.strictObject({
  type: z.literal('state'),
  snapshot: roomViewSchema,
});

const commandErrorMessageSchema = z.strictObject({
  type: z.literal('command_error'),
  command_id: z.string().nullable(),
  code: z.string(),
  message: z.string(),
});

const connectionErrorMessageSchema = z.strictObject({
  type: z.literal('connection_error'),
  code: z.string(),
  message: z.string(),
});

export const serverMessageSchema = z.discriminatedUnion('type', [
  connectedMessageSchema,
  commandAckMessageSchema,
  stateMessageSchema,
  commandErrorMessageSchema,
  connectionErrorMessageSchema,
]);

export type ConnectFrame = z.infer<typeof connectFrameSchema>;
export type RoomView = z.infer<typeof roomViewSchema>;
export type ServerMessage = z.infer<typeof serverMessageSchema>;
