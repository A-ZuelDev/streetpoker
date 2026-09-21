import type { RoomView } from '../realtime/messages';

export function openRoomSnapshot(roomName = 'Friday Night'): RoomView {
  return {
    room: {
      room_id: 'internal-room-id',
      room_code: 'ABCDEFGH',
      status: 'open',
      host_guest_id: 'guest_host',
      settings: {
        room_name: roomName,
        small_blind: 50,
        big_blind: 100,
        default_starting_stack: 10_000,
        seating_approval_required: true,
        stand_up_enabled: false,
        stand_up_penalty_per_recipient_chips: 100,
        max_seats: 6,
        password_protected: false,
      },
      members: [
        {
          guest_id: 'guest_host',
          nickname: 'Mara',
          status: 'in_room',
          is_host: true,
          stack: null,
        },
        {
          guest_id: 'guest_alice',
          nickname: 'Alice',
          status: 'seated',
          is_host: false,
          stack: 10_000,
        },
      ],
      seats: [
        { seat_index: 0, guest_id: null, nickname: null, stack: null },
        {
          seat_index: 1,
          guest_id: 'guest_alice',
          nickname: 'Alice',
          stack: 10_000,
        },
        { seat_index: 2, guest_id: null, nickname: null, stack: null },
        { seat_index: 3, guest_id: null, nickname: null, stack: null },
        { seat_index: 4, guest_id: null, nickname: null, stack: null },
        { seat_index: 5, guest_id: null, nickname: null, stack: null },
      ],
      seat_requests: [],
      stand_up: {
        active_round: null,
        last_result: null,
      },
    },
    next_hand_number: 1,
    active_hand: null,
    last_hand: null,
  };
}

export function activeRoomSnapshot(roomName = 'Friday Night'): RoomView {
  const snapshot = openRoomSnapshot(roomName);
  snapshot.room.status = 'hand_in_progress';
  snapshot.room.members = [
    {
      guest_id: 'guest_host',
      nickname: 'Mara',
      status: 'seated',
      is_host: true,
      stack: 9_850,
    },
    {
      guest_id: 'guest_alice',
      nickname: 'Alice',
      status: 'seated',
      is_host: false,
      stack: 9_900,
    },
    {
      guest_id: 'guest_june',
      nickname: 'June',
      status: 'seated',
      is_host: false,
      stack: 10_000,
    },
  ];
  snapshot.room.seats = [
    {
      seat_index: 0,
      guest_id: 'guest_host',
      nickname: 'Mara',
      stack: 9_850,
    },
    {
      seat_index: 1,
      guest_id: 'guest_alice',
      nickname: 'Alice',
      stack: 9_900,
    },
    {
      seat_index: 2,
      guest_id: 'guest_june',
      nickname: 'June',
      stack: 10_000,
    },
    { seat_index: 3, guest_id: null, nickname: null, stack: null },
    { seat_index: 4, guest_id: null, nickname: null, stack: null },
    { seat_index: 5, guest_id: null, nickname: null, stack: null },
  ];
  snapshot.active_hand = {
    hand_number: 1,
    action_sequence: 2,
    action_deadline_unix_ms: 1_800_000_000_000,
    current_actor_timebank_ms: 60_000,
    current_actor_using_timebank: false,
    phase: 'flop',
    button_seat: 2,
    small_blind_seat: 0,
    big_blind_seat: 1,
    board: [
      { rank: 'ace', suit: 'spades' },
      { rank: 'ten', suit: 'hearts' },
      { rank: 'two', suit: 'clubs' },
    ],
    pot_chips: 250,
    players: [
      {
        guest_id: 'guest_host',
        nickname: 'Mara',
        seat_index: 0,
        status: 'active',
        current_stack: 9_850,
        gross_committed: 150,
        street_committed: 50,
        hole_cards: [
          { rank: 'king', suit: 'spades' },
          { rank: 'queen', suit: 'spades' },
        ],
      },
      {
        guest_id: 'guest_alice',
        nickname: 'Alice',
        seat_index: 1,
        status: 'all_in',
        current_stack: 9_900,
        gross_committed: 100,
        street_committed: 100,
        hole_cards: null,
      },
    ],
    current_actor: 'guest_host',
    legal_actions: {
      actor: 'guest_host',
      kinds: ['fold', 'call', 'raise'],
      amount_to_call: 50,
      call: { chips: 50, total: 100, is_all_in: false },
      bet_to: null,
      raise_to: {
        minimum_full_to: 200,
        maximum_to: 9_900,
        short_all_in_to: null,
      },
      raise_reopened: true,
    },
  };
  return snapshot;
}

export function completedRoomSnapshot(): RoomView {
  const snapshot = openRoomSnapshot();
  snapshot.next_hand_number = 2;
  snapshot.last_hand = {
    hand_number: 1,
    final_action_sequence: 3,
    phase: 'complete_by_fold',
    source: 'complete_by_fold',
    button_seat: 2,
    board: [{ rank: 'ace', suit: 'spades' }],
    players: [
      {
        guest_id: 'guest_host',
        nickname: 'Mara',
        seat_index: 0,
        folded: false,
        final_stack: 10_100,
        total_award: 200,
        gross_committed: 100,
        returned_excess: 0,
        hole_cards: [
          { rank: 'king', suit: 'spades' },
          { rank: 'queen', suit: 'spades' },
        ],
      },
      {
        guest_id: 'guest_alice',
        nickname: 'Alice',
        seat_index: 1,
        folded: true,
        final_stack: 9_900,
        total_award: 0,
        gross_committed: 100,
        returned_excess: 0,
        hole_cards: null,
      },
    ],
    pots: [
      {
        pot_index: 0,
        amount: 200,
        winners: [
          { guest_id: 'guest_host', chips: 200, receives_odd_chip: false },
        ],
      },
    ],
  };
  return snapshot;
}
