import { describe, expect, it } from 'vitest';

import { activeRoomSnapshot, openRoomSnapshot } from '../test/roomSnapshots';
import {
  buildRoomCommand,
  roomNameTransportLimit,
  roomCommandSchema,
  safeRoomCommandMessage,
} from './roomCommands';

const valid = [
  { type: 'request_seat', command_id: '1', seat_index: 5 },
  { type: 'approve_seat', command_id: '2', target_guest_id: 'guest_alice' },
  { type: 'reject_seat', command_id: '3', target_guest_id: 'guest_alice' },
  { type: 'stand', command_id: '4' },
  { type: 'leave', command_id: '5' },
  { type: 'kick', command_id: '6', target_guest_id: 'guest_alice' },
  { type: 'start_hand', command_id: '7', hand_number: 1 },
  { type: 'update_settings', command_id: '8', password: null },
  { type: 'close_room', command_id: '9' },
];

describe('room commands', () => {
  it.each(valid)('accepts the exact $type command', (command) => {
    expect(roomCommandSchema.parse(command)).toEqual(command);
  });

  it.each([
    { type: 'stand' },
    { type: 'request_seat', command_id: 'x' },
    { type: 'approve_seat', command_id: 'x' },
    { type: 'start_hand', command_id: 'x' },
    { type: 'update_settings', command_id: 'x' },
  ])('rejects missing fields for $type', (command) => {
    expect(roomCommandSchema.safeParse(command).success).toBe(false);
  });

  it.each([
    { type: 'stand', command_id: '', extra: true },
    { type: 'stand', command_id: ' '.repeat(3) },
    { type: 'stand', command_id: 'x'.repeat(65) },
    { type: 'request_seat', command_id: 'x', seat_index: -1 },
    { type: 'request_seat', command_id: 'x', seat_index: 6 },
    { type: 'request_seat', command_id: 'x', seat_index: 1.5 },
    { type: 'request_seat', command_id: 'x', seat_index: true },
    { type: 'start_hand', command_id: 'x', hand_number: 0 },
    { type: 'start_hand', command_id: 'x', hand_number: 1.5 },
    { type: 'start_hand', command_id: 'x', hand_number: true },
    {
      type: 'start_hand',
      command_id: 'x',
      hand_number: Number.MAX_SAFE_INTEGER + 1,
    },
    { type: 'kick', command_id: 'x', target_guest_id: '' },
    { type: 'kick', command_id: 'x', target_guest_id: 'x'.repeat(129) },
    { type: 'update_settings', command_id: 'x', room_name: null },
    { type: 'update_settings', command_id: 'x', max_seats: 6 },
    { type: 'update_settings', command_id: 'x', room_code: 'ABCDEFGH' },
    { type: 'update_settings', command_id: 'x', actor_guest_id: 'guest_host' },
    { type: 'update_settings', command_id: 'x', room_id: 'room' },
    {
      type: 'update_settings',
      command_id: 'x',
      room_name: 'x'.repeat(roomNameTransportLimit + 1),
    },
    { type: 'connect', guest_token: 'A'.repeat(43) },
    {
      type: 'fold',
      command_id: 'x',
      hand_number: 1,
      expected_action_sequence: 0,
    },
  ])('rejects invalid or foreign frames', (command) => {
    expect(roomCommandSchema.safeParse(command).success).toBe(false);
  });

  it('accepts all writable settings and only password null', () => {
    const command = {
      type: 'update_settings' as const,
      command_id: 'settings',
      room_name: 'New room',
      small_blind: 100,
      big_blind: 200,
      default_starting_stack: 20_000,
      seating_approval_required: false,
      password: 'new password',
    };
    expect(roomCommandSchema.parse(command)).toEqual(command);
  });

  it('builds start_hand only from authoritative next_hand_number', () => {
    const snapshot = openRoomSnapshot();
    snapshot.next_hand_number = 37;
    expect(
      buildRoomCommand({
        snapshot,
        guestId: 'guest_host',
        request: { type: 'start_hand' },
        commandId: 'start',
      }),
    ).toEqual({ type: 'start_hand', command_id: 'start', hand_number: 37 });
  });

  it('uses request_seat for approval-disabled seating and blocks it during a hand', () => {
    const open = openRoomSnapshot();
    open.room.settings.seating_approval_required = false;
    expect(
      buildRoomCommand({
        snapshot: open,
        guestId: 'guest_host',
        request: { type: 'request_seat', seatIndex: 0 },
        commandId: 'seat',
      })?.type,
    ).toBe('request_seat');

    const active = activeRoomSnapshot();
    active.room.members[0]!.status = 'in_room';
    active.room.seats[0] = {
      seat_index: 0,
      guest_id: null,
      nickname: null,
      stack: null,
    };
    active.room.settings.seating_approval_required = false;
    expect(
      buildRoomCommand({
        snapshot: active,
        guestId: 'guest_host',
        request: { type: 'request_seat', seatIndex: 0 },
        commandId: 'seat',
      }),
    ).toBeNull();
  });

  it('maps known and unknown errors without server text', () => {
    expect(safeRoomCommandMessage('seat_occupied')).toBe(
      'That seat is no longer available.',
    );
    expect(safeRoomCommandMessage('future_private_detail')).toBe(
      'The server rejected the room action.',
    );
  });
});
