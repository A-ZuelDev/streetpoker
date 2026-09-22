import type { RoomView } from '../../realtime/messages';
import type {
  CardRank,
  CardView,
  HandCompletionView,
  MemberView,
  OccupiedSeatView,
  PlayerState,
  SeatPosition,
  SeatView,
  TableView,
  RoomPendingView,
  StandUpView,
} from './table.types';
import type { ConnectionStatus } from '../../realtime/realtimeStore';
import type { PendingPokerCommand } from '../../realtime/pokerActions';
import type { PendingRoomCommand } from '../../realtime/roomCommands';
import { deriveLiveActionModel } from './liveActionModel';

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
  options?: {
    viewerGuestId: string;
    hostGuestId: string;
    showKick: boolean;
    canKick: boolean;
  },
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
    ...(options === undefined ? {} : { guestId: member.guest_id }),
    nickname: member.nickname,
    status,
    stack: member.stack,
    isHost:
      options === undefined
        ? member.is_host
        : member.guest_id === options.hostGuestId,
    ...(options === undefined
      ? {}
      : {
          isViewer: member.guest_id === options.viewerGuestId,
          showKick: options.showKick && member.guest_id !== options.hostGuestId,
          canKick: options.canKick && member.guest_id !== options.hostGuestId,
        }),
  };
}

function roomPendingView(
  pending: PendingRoomCommand | null,
): RoomPendingView | null {
  if (pending === null) {
    return null;
  }

  const kindByCommand = {
    request_seat: 'request-seat',
    approve_seat: 'approve-seat',
    reject_seat: 'reject-seat',
    stand: 'stand',
    leave: 'leave',
    kick: 'kick',
    start_hand: 'start-hand',
    pause_game: 'pause-game',
    resume_game: 'resume-game',
    update_settings: 'settings',
    close_room: 'close-room',
  } as const;
  const submittingLabelByCommand = {
    request_seat:
      pending.seatIndex === undefined
        ? 'Requesting seat…'
        : `Requesting seat ${pending.seatIndex + 1}…`,
    approve_seat: 'Approving seat request…',
    reject_seat: 'Rejecting seat request…',
    stand: 'Standing up…',
    leave: 'Leaving room…',
    kick: 'Removing player…',
    start_hand: 'Starting hand…',
    pause_game: 'Pausing game…',
    resume_game: 'Resuming game…',
    update_settings: 'Saving room settings…',
    close_room: 'Closing room…',
  } as const;

  return {
    kind: kindByCommand[pending.type],
    phase: pending.acknowledged ? 'waiting' : 'submitting',
    label: pending.acknowledged
      ? 'Waiting for the table to update…'
      : submittingLabelByCommand[pending.type],
  };
}

function handCompletionView(
  completed: RoomView['last_hand'],
): HandCompletionView | null {
  if (completed === null) {
    return null;
  }

  return {
    handNumber: completed.hand_number,
    awards: completed.players
      .filter((player) => player.total_award > 0)
      .map((player) => ({
        displayName: player.nickname,
        seatNumber: player.seat_index + 1,
        amount: player.total_award,
      })),
  };
}

const cancellationLabels = {
  participant_left: 'A participant left the room.',
  participant_kicked: 'A participant was removed from the room.',
  participant_vacated_seat: 'A participant left their poker seat.',
  participant_busted: 'A participant ran out of chips.',
  disabled: 'The host turned Stand-Up off.',
  room_closed: 'The room was closed.',
} as const;

function standUpView(snapshot: RoomView): StandUpView {
  const state = snapshot.room.stand_up;
  const active = state.active_round;
  const result = state.last_result;
  return {
    enabled: snapshot.room.settings.stand_up_enabled,
    penaltyPerRecipientChips:
      active?.penalty_per_recipient_chips ??
      (result?.type === 'resolution'
        ? result.penalty_per_recipient_chips
        : snapshot.room.settings.stand_up_penalty_per_recipient_chips),
    activeRound:
      active === null
        ? null
        : {
            startHandNumber: active.start_hand_number,
            atRiskSeatNumbers: active.participants
              .filter((participant) => !participant.is_cleared)
              .map((participant) => participant.seat_index + 1),
            clearedSeatNumbers: active.participants
              .filter((participant) => participant.is_cleared)
              .map((participant) => participant.seat_index + 1),
          },
    lastResult:
      result === null
        ? null
        : result.type === 'resolution'
          ? {
              kind: 'resolution',
              handNumber: result.hand_number,
              squidSeatNumber: result.squid_seat_index + 1,
              intendedTotal: result.intended_total,
              actualTotal: result.actual_total,
              shortfall: result.shortfall,
              transfers: result.transfers.map((transfer) => ({
                toSeatNumber: transfer.to_seat_index + 1,
                chips: transfer.chips,
              })),
            }
          : {
              kind: 'cancellation',
              reason: cancellationLabels[result.reason],
            },
  };
}

export function roomSnapshotToTableView(
  snapshot: RoomView,
  viewerGuestId: string,
  connectionStatus: ConnectionStatus = 'connected',
  pendingPokerCommand: PendingPokerCommand | null = null,
  pendingRoomCommand: PendingRoomCommand | null = null,
): TableView {
  const activeHand = snapshot.active_hand;
  const activeByGuest = new Map(
    activeHand?.players.map((player) => [player.guest_id, player]) ?? [],
  );
  const actor = snapshot.room.members.find(
    (member) => member.guest_id === viewerGuestId,
  );
  const isHost = snapshot.room.host_guest_id === viewerGuestId;
  const stateConsistent =
    snapshot.room.status === 'hand_in_progress'
      ? activeHand !== null
      : activeHand === null;
  const fresh = connectionStatus === 'connected' && stateConsistent;
  const commandsPending =
    pendingPokerCommand !== null || pendingRoomCommand !== null;
  const pendingRoomView = roomPendingView(pendingRoomCommand);
  const actorHasRequest = snapshot.room.seat_requests.some(
    (request) => request.guest_id === viewerGuestId,
  );
  const requestedSeatIndexes = new Set(
    snapshot.room.seat_requests.map((request) => request.seat_index),
  );
  const standUp = standUpView(snapshot);
  const activeStandUpBySeat = new Map(
    snapshot.room.stand_up.active_round?.participants.map((participant) => [
      participant.seat_index,
      participant.is_cleared ? ('cleared' as const) : ('at-risk' as const),
    ]) ?? [],
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
        const requested = requestedSeatIndexes.has(seat.seat_index);
        const canRequest =
          fresh &&
          !commandsPending &&
          actor?.status === 'in_room' &&
          !actorHasRequest &&
          !requested &&
          snapshot.room.status !== 'closed' &&
          !(
            snapshot.room.status === 'hand_in_progress' &&
            !snapshot.room.settings.seating_approval_required
          );
        return {
          kind: 'empty',
          seatIndex: seat.seat_index,
          position,
          requested,
          canRequest,
          requestLabel: requested
            ? `Seat ${seat.seat_index + 1} requested`
            : snapshot.room.settings.seating_approval_required
              ? `Request seat ${seat.seat_index + 1}`
              : `Take seat ${seat.seat_index + 1}`,
        };
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
        standUpStatus: activeStandUpBySeat.get(seat.seat_index) ?? null,
      };
    });
  const viewerHasSeat = seats.some(
    (seat) => seat.kind === 'occupied' && seat.isHero,
  );
  const currentActorNickname =
    activeHand?.players.find(
      (player) => player.guest_id === activeHand.current_actor,
    )?.nickname ?? null;

  return {
    mode: 'live',
    variant: activeHand === null ? 'open' : 'active',
    isHandActive: activeHand !== null,
    roomName: snapshot.room.settings.room_name,
    roomCode: snapshot.room.room_code,
    smallBlind: snapshot.room.settings.small_blind,
    bigBlind: snapshot.room.settings.big_blind,
    isHost,
    street: activeHand === null ? 'Open table' : phaseLabel(activeHand.phase),
    actionDeadlineUnixMs: activeHand?.action_deadline_unix_ms ?? null,
    actionTimerRemainingMs: activeHand?.action_timer_remaining_ms ?? null,
    currentActorTimebankMs: activeHand?.current_actor_timebank_ms ?? null,
    currentActorTimebankTotalMs:
      activeHand?.current_actor_timebank_total_ms ?? null,
    currentActorUsingTimebank:
      activeHand?.current_actor_using_timebank ?? false,
    timebankRefillAmountMs: activeHand?.timebank_refill_amount_ms ?? null,
    timebankRefillHandsRemaining:
      activeHand?.timebank_refill_hands_remaining ?? null,
    isPaused: snapshot.room.is_paused,
    pot: activeHand?.pot_chips ?? 0,
    board: activeHand?.board.map(cardView) ?? [],
    seats,
    legalActions: null,
    liveActions: deriveLiveActionModel({
      activeHand,
      viewerGuestId,
      viewerHasSeat,
      currentActorNickname,
      connectionStatus,
      pendingCommand: pendingPokerCommand,
      roomCommandPending: pendingRoomCommand !== null,
      roomPendingLabel: pendingRoomView?.label ?? null,
      viewerIsHost: isHost,
      hasCompletedHand: snapshot.last_hand !== null,
      isPaused: snapshot.room.is_paused,
    }),
    handCompletion:
      activeHand === null ? handCompletionView(snapshot.last_hand) : null,
    standUp,
    roomPanel: {
      members: snapshot.room.members.map((member) =>
        memberView(member, activeByGuest.get(member.guest_id), {
          viewerGuestId,
          hostGuestId: snapshot.room.host_guest_id,
          showKick: isHost,
          canKick:
            fresh &&
            !commandsPending &&
            isHost &&
            (snapshot.room.status === 'open' || member.status === 'in_room'),
        }),
      ),
      seatRequests: snapshot.room.seat_requests
        .filter((request) => isHost || request.guest_id === viewerGuestId)
        .map((request) => ({
          guestId: request.guest_id,
          nickname: request.nickname,
          seatIndex: request.seat_index,
          isViewer: request.guest_id === viewerGuestId,
          canApprove:
            fresh &&
            !commandsPending &&
            isHost &&
            snapshot.room.status === 'open',
          canReject: fresh && !commandsPending && isHost,
        })),
      canStartHand:
        fresh &&
        !commandsPending &&
        isHost &&
        snapshot.room.status === 'open' &&
        activeHand === null,
      isHost,
      canStand:
        fresh &&
        !commandsPending &&
        actor?.status === 'seated' &&
        snapshot.room.status === 'open',
      canLeave:
        fresh &&
        !commandsPending &&
        !isHost &&
        actor !== undefined &&
        (snapshot.room.status === 'open' || actor.status === 'in_room'),
      canCloseRoom:
        fresh &&
        !commandsPending &&
        isHost &&
        snapshot.room.status === 'open' &&
        activeHand === null,
      canPauseGame:
        fresh &&
        !commandsPending &&
        isHost &&
        activeHand !== null &&
        !snapshot.room.is_paused,
      canResumeGame:
        fresh &&
        !commandsPending &&
        isHost &&
        activeHand !== null &&
        snapshot.room.is_paused,
      isPaused: snapshot.room.is_paused,
      showStand: actor?.status === 'seated',
      startHandLabel:
        snapshot.last_hand === null ? 'Start hand' : 'Start next hand',
      pendingCommand: pendingRoomView,
      controlsDisabled: !fresh || commandsPending,
      handInProgress: snapshot.room.status === 'hand_in_progress',
      standUpActive: standUp.activeRound !== null,
      settings: {
        roomName: snapshot.room.settings.room_name,
        smallBlind: snapshot.room.settings.small_blind,
        bigBlind: snapshot.room.settings.big_blind,
        defaultStartingStack: snapshot.room.settings.default_starting_stack,
        actionTimeMs: snapshot.room.settings.action_time_ms,
        timebankTotalMs: snapshot.room.settings.timebank_total_ms,
        timebankRefillAmountMs:
          snapshot.room.settings.timebank_refill_amount_ms,
        timebankRefillEveryHands:
          snapshot.room.settings.timebank_refill_every_hands,
        seatingApprovalRequired:
          snapshot.room.settings.seating_approval_required,
        standUpEnabled: snapshot.room.settings.stand_up_enabled,
        standUpPenaltyPerRecipientChips:
          snapshot.room.settings.stand_up_penalty_per_recipient_chips,
        maxSeats: snapshot.room.settings.max_seats,
        passwordProtected: snapshot.room.settings.password_protected,
      },
    },
    chat: null,
  };
}
