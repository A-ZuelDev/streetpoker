import { z } from 'zod';

import type { RoomView } from './messages';
import { standUpPenaltyPerRecipientMaximum } from './protocolLimits';

const safeIntegerSchema = z
  .number()
  .refine(Number.isSafeInteger, 'Expected a safe integer.');
const positiveSafeIntegerSchema = safeIntegerSchema.refine(
  (value) => value >= 1,
  'Expected a positive safe integer.',
);
const standUpPenaltySchema = positiveSafeIntegerSchema.refine(
  (value) => value <= standUpPenaltyPerRecipientMaximum,
  'Expected a six-max safe Stand-Up penalty.',
);
const seatIndexSchema = safeIntegerSchema.refine(
  (value) => value >= 0 && value <= 5,
  'Expected a six-max seat index.',
);
const commandIdSchema = z.string().trim().min(1).max(64);
const targetGuestIdSchema = z.string().min(1).max(128);
export const roomNameTransportLimit = 4_096;

export const requestSeatCommandSchema = z.strictObject({
  type: z.literal('request_seat'),
  command_id: commandIdSchema,
  seat_index: seatIndexSchema,
});

export const approveSeatCommandSchema = z.strictObject({
  type: z.literal('approve_seat'),
  command_id: commandIdSchema,
  target_guest_id: targetGuestIdSchema,
});

export const rejectSeatCommandSchema = z.strictObject({
  type: z.literal('reject_seat'),
  command_id: commandIdSchema,
  target_guest_id: targetGuestIdSchema,
});

export const standCommandSchema = z.strictObject({
  type: z.literal('stand'),
  command_id: commandIdSchema,
});

export const leaveCommandSchema = z.strictObject({
  type: z.literal('leave'),
  command_id: commandIdSchema,
});

export const kickCommandSchema = z.strictObject({
  type: z.literal('kick'),
  command_id: commandIdSchema,
  target_guest_id: targetGuestIdSchema,
});

export const startHandCommandSchema = z.strictObject({
  type: z.literal('start_hand'),
  command_id: commandIdSchema,
  hand_number: positiveSafeIntegerSchema,
});

const settingKeys = [
  'room_name',
  'small_blind',
  'big_blind',
  'default_starting_stack',
  'seating_approval_required',
  'stand_up_enabled',
  'stand_up_penalty_per_recipient_chips',
  'password',
] as const;

export const updateSettingsCommandSchema = z
  .strictObject({
    type: z.literal('update_settings'),
    command_id: commandIdSchema,
    room_name: z.string().max(roomNameTransportLimit).optional(),
    small_blind: positiveSafeIntegerSchema.optional(),
    big_blind: positiveSafeIntegerSchema.optional(),
    default_starting_stack: positiveSafeIntegerSchema.optional(),
    seating_approval_required: z.boolean().optional(),
    stand_up_enabled: z.boolean().optional(),
    stand_up_penalty_per_recipient_chips: standUpPenaltySchema.optional(),
    password: z.string().max(128).nullable().optional(),
  })
  .refine(
    (command) => settingKeys.some((key) => Object.hasOwn(command, key)),
    'At least one room setting is required.',
  );

export const closeRoomCommandSchema = z.strictObject({
  type: z.literal('close_room'),
  command_id: commandIdSchema,
});

export const roomCommandSchema = z.discriminatedUnion('type', [
  requestSeatCommandSchema,
  approveSeatCommandSchema,
  rejectSeatCommandSchema,
  standCommandSchema,
  leaveCommandSchema,
  kickCommandSchema,
  startHandCommandSchema,
  updateSettingsCommandSchema,
  closeRoomCommandSchema,
]);

export type RoomCommand = z.infer<typeof roomCommandSchema>;
export type RoomCommandType = RoomCommand['type'];
export type RoomSettingsPatch = Omit<
  z.input<typeof updateSettingsCommandSchema>,
  'type' | 'command_id'
>;

export type RoomCommandRequest =
  | { readonly type: 'request_seat'; readonly seatIndex: number }
  | {
      readonly type: 'approve_seat' | 'reject_seat' | 'kick';
      readonly targetGuestId: string;
    }
  | {
      readonly type: 'stand' | 'leave' | 'start_hand' | 'close_room';
    }
  | {
      readonly type: 'update_settings';
      readonly patch: RoomSettingsPatch;
    };

export interface PendingRoomCommand {
  readonly commandId: string;
  readonly type: RoomCommandType;
  readonly targetGuestId?: string;
  readonly seatIndex?: number;
  readonly acknowledged: boolean;
}

function roomStateIsConsistent(snapshot: RoomView): boolean {
  if (snapshot.room.status === 'hand_in_progress') {
    return snapshot.active_hand !== null;
  }
  return snapshot.active_hand === null;
}

export function buildRoomCommand(options: {
  readonly snapshot: RoomView;
  readonly guestId: string;
  readonly request: RoomCommandRequest;
  readonly commandId: string;
}): RoomCommand | null {
  const { snapshot, guestId, request, commandId } = options;
  const room = snapshot.room;
  const actor = room.members.find((member) => member.guest_id === guestId);
  if (
    actor === undefined ||
    room.status === 'closed' ||
    !roomStateIsConsistent(snapshot)
  ) {
    return null;
  }
  const isHost = guestId === room.host_guest_id;
  const common = { type: request.type, command_id: commandId } as const;

  switch (request.type) {
    case 'request_seat': {
      const seat = room.seats.find(
        (candidate) => candidate.seat_index === request.seatIndex,
      );
      const seatRequested = room.seat_requests.some(
        (candidate) => candidate.seat_index === request.seatIndex,
      );
      const actorRequested = room.seat_requests.some(
        (candidate) => candidate.guest_id === guestId,
      );
      if (
        actor.status === 'seated' ||
        actorRequested ||
        seat === undefined ||
        seat.guest_id !== null ||
        seatRequested ||
        (room.status === 'hand_in_progress' &&
          !room.settings.seating_approval_required)
      ) {
        return null;
      }
      return roomCommandSchema.parse({
        ...common,
        seat_index: request.seatIndex,
      });
    }
    case 'approve_seat': {
      if (
        !isHost ||
        room.status !== 'open' ||
        !room.seat_requests.some(
          (candidate) => candidate.guest_id === request.targetGuestId,
        )
      ) {
        return null;
      }
      return roomCommandSchema.parse({
        ...common,
        target_guest_id: request.targetGuestId,
      });
    }
    case 'reject_seat': {
      if (
        !isHost ||
        !room.seat_requests.some(
          (candidate) => candidate.guest_id === request.targetGuestId,
        )
      ) {
        return null;
      }
      return roomCommandSchema.parse({
        ...common,
        target_guest_id: request.targetGuestId,
      });
    }
    case 'stand':
      if (actor.status !== 'seated' || room.status !== 'open') {
        return null;
      }
      return roomCommandSchema.parse(common);
    case 'leave':
      if (
        isHost ||
        (room.status === 'hand_in_progress' && actor.status === 'seated')
      ) {
        return null;
      }
      return roomCommandSchema.parse(common);
    case 'kick': {
      const target = room.members.find(
        (member) => member.guest_id === request.targetGuestId,
      );
      if (
        !isHost ||
        target === undefined ||
        target.guest_id === guestId ||
        target.guest_id === room.host_guest_id ||
        (room.status === 'hand_in_progress' && target.status === 'seated')
      ) {
        return null;
      }
      return roomCommandSchema.parse({
        ...common,
        target_guest_id: request.targetGuestId,
      });
    }
    case 'start_hand':
      if (!isHost || room.status !== 'open') {
        return null;
      }
      return roomCommandSchema.parse({
        ...common,
        hand_number: snapshot.next_hand_number,
      });
    case 'update_settings': {
      if (
        !isHost ||
        (room.status === 'hand_in_progress' &&
          (Object.hasOwn(request.patch, 'small_blind') ||
            Object.hasOwn(request.patch, 'big_blind') ||
            Object.hasOwn(request.patch, 'default_starting_stack')))
      ) {
        return null;
      }
      return roomCommandSchema.parse({ ...common, ...request.patch });
    }
    case 'close_room':
      if (!isHost || room.status !== 'open') {
        return null;
      }
      return roomCommandSchema.parse(common);
  }
}

const safeRoomCommandMessages: Record<string, string> = {
  room_closed: 'This room is closed.',
  not_room_member: 'You are no longer a member of this room.',
  invalid_seat: 'Choose a valid seat.',
  already_seated: 'You are already seated.',
  duplicate_seat_request: 'You already have a pending seat request.',
  seat_occupied: 'That seat is no longer available.',
  seat_already_requested: 'Another player already requested that seat.',
  active_hand_mutation: 'That room change is unavailable during a hand.',
  not_room_host: 'Only the room host can do that.',
  invalid_guest_id: 'That room member is no longer available.',
  seat_request_not_found: 'That seat request is no longer pending.',
  member_not_found: 'That room member is no longer available.',
  not_seated: 'You are not currently seated.',
  host_cannot_leave: 'The host must close the room instead of leaving.',
  cannot_kick_host: 'The room host cannot be removed.',
  hand_already_active: 'A hand is already in progress.',
  stale_game_state: 'The room changed before the hand could start.',
  insufficient_players: 'At least two eligible seated players are required.',
  cannot_start_hand: 'The room could not start a hand.',
  invalid_room_name: 'Enter a valid room name.',
  invalid_settings: 'The room settings are invalid.',
  invalid_room_password: 'The room password is invalid.',
  validation_error: 'The room command was rejected.',
  room_not_found: 'The room is no longer available.',
  internal_error: 'The server could not complete the room action.',
};

export function safeRoomCommandMessage(code: string): string {
  return (
    safeRoomCommandMessages[code] ?? 'The server rejected the room action.'
  );
}
