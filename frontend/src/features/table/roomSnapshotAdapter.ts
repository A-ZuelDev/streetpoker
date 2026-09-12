import type { RoomView } from '../../realtime/messages';
import type {
  CardRank,
  CardView,
  MemberView,
  OccupiedSeatView,
  PlayerState,
  SeatPosition,
  SeatView,
  TableView,
} from './table.types';

const positions = [
  'bottom-center',
  'lower-left',
  'upper-left',
  'top-center',
  'upper-right',
  'lower-right',
] as const satisfies readonly SeatPosition[];

const visibleRanks = {
  two: '2',
  three: '3',
  four: '4',
  five: '5',
  six: '6',
  seven: '7',
  eight: '8',
  nine: '9',
  ten: '10',
  jack: 'J',
  queen: 'Q',
  king: 'K',
  ace: 'A',
} as const satisfies Record<RoomViewCard['rank'], CardRank>;

type RoomViewCard = NonNullable<
  NonNullable<RoomView['active_hand']>['players'][number]['hole_cards']
>[number];
type ActivePlayer = NonNullable<RoomView['active_hand']>['players'][number];

function cardView(card: RoomViewCard): CardView {
  return { rank: visibleRanks[card.rank], suit: card.suit };
}

function phaseLabel(
  phase: NonNullable<RoomView['active_hand']>['phase'],
): string {
  const labels = {
    preflop: 'Preflop',
    flop: 'Flop',
    turn: 'Turn',
    river: 'River',
    showdown_ready: 'Showdown',
    complete_by_fold: 'Hand complete',
  } as const;
  return labels[phase];
}

function playerState(player: ActivePlayer | undefined): PlayerState {
  if (player === undefined) {
    return 'not-in-hand';
  }
  if (player.status === 'all_in') {
    return 'all-in';
  }
  return player.status;
}

function memberView(
  member: RoomView['room']['members'][number],
  activePlayer: ActivePlayer | undefined,
): MemberView {
  const status =
    activePlayer?.status === 'all_in'
      ? 'All in'
      : activePlayer?.status === 'folded'
        ? 'Folded'
        : activePlayer?.status === 'active'
          ? 'In hand'
          : member.status === 'seated'
            ? 'Seated'
            : 'In room';
  return {
    nickname: member.nickname,
    status,
    stack: member.stack,
    isHost: member.is_host,
  };
}

export function roomSnapshotToTableView(
  snapshot: RoomView,
  viewerGuestId: string,
): TableView {
  const activeHand = snapshot.active_hand;
  const activeByGuest = new Map(
    activeHand?.players.map((player) => [player.guest_id, player]) ?? [],
  );
  const seats = [...snapshot.room.seats]
    .sort((left, right) => left.seat_index - right.seat_index)
    .map<SeatView>((seat) => {
      const position = positions[seat.seat_index];
      if (position === undefined) {
        throw new Error('The server sent an invalid seat position.');
      }
      if (
        seat.guest_id === null ||
        seat.nickname === null ||
        seat.stack === null
      ) {
        return { kind: 'empty', seatIndex: seat.seat_index, position };
      }

      const player = activeByGuest.get(seat.guest_id);
      const isHero = seat.guest_id === viewerGuestId;
      const cards: OccupiedSeatView['cards'] =
        player === undefined
          ? null
          : isHero && player.hole_cards !== null
            ? player.hole_cards.map(cardView)
            : 'concealed';
      return {
        kind: 'occupied',
        seatIndex: seat.seat_index,
        position,
        nickname: seat.nickname,
        stack: seat.stack,
        contribution: player?.street_committed ?? null,
        state: activeHand === null ? 'seated' : playerState(player),
        isHero,
        isActing: activeHand?.current_actor === seat.guest_id,
        isDealer: activeHand?.button_seat === seat.seat_index,
        blind:
          activeHand?.small_blind_seat === seat.seat_index
            ? 'small-blind'
            : activeHand?.big_blind_seat === seat.seat_index
              ? 'big-blind'
              : null,
        cards,
      };
    });

  return {
    mode: 'live',
    variant: activeHand === null ? 'open' : 'active',
    isHandActive: activeHand !== null,
    roomName: snapshot.room.settings.room_name,
    roomCode: snapshot.room.room_code,
    smallBlind: snapshot.room.settings.small_blind,
    bigBlind: snapshot.room.settings.big_blind,
    isHost: snapshot.room.host_guest_id === viewerGuestId,
    street: activeHand === null ? 'Open table' : phaseLabel(activeHand.phase),
    pot: activeHand?.pot_chips ?? 0,
    board: activeHand?.board.map(cardView) ?? [],
    seats,
    legalActions: null,
    roomPanel: {
      members: snapshot.room.members.map((member) =>
        memberView(member, activeByGuest.get(member.guest_id)),
      ),
      seatRequests: snapshot.room.seat_requests.map((request) => ({
        nickname: request.nickname,
        seatIndex: request.seat_index,
      })),
      canStartHand: false,
    },
    chat: null,
  };
}
