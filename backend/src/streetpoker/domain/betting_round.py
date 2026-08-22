"""Pure, deterministic mechanics for one no-limit betting round."""

from collections.abc import Iterable
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Self

from streetpoker.domain.chips import ChipStack
from streetpoker.domain.errors import (
    BettingRoundCompleteError,
    DuplicateBettingPlayerError,
    DuplicateBettingSeatError,
    IllegalBetError,
    IllegalCallError,
    IllegalCheckError,
    IllegalRaiseError,
    InvalidActionTypeError,
    InvalidBettingParticipantError,
    InvalidBettingRoundStateError,
    InvalidMinimumBetError,
    InvalidWagerAmountError,
    OutOfTurnError,
    PlayerNotInBettingRoundError,
    RaiseNotReopenedError,
    WagerBelowMinimumError,
    WagerExceedsStackError,
)
from streetpoker.domain.players import PlayerId
from streetpoker.domain.table import SeatIndex


class BettingParticipantStatus(StrEnum):
    """A participant's status inside one betting round."""

    ACTIVE = "active"
    FOLDED = "folded"
    ALL_IN = "all_in"


class ActionKind(StrEnum):
    """Stable identifiers for actions exposed by legal-action calculation."""

    CHECK = "check"
    CALL = "call"
    BET = "bet"
    RAISE = "raise"
    FOLD = "fold"


class WagerClassification(StrEnum):
    """How an action changed the current wager."""

    NONE = "none"
    FULL_BET = "full_bet"
    SHORT_ALL_IN_BET = "short_all_in_bet"
    FULL_RAISE = "full_raise"
    SHORT_ALL_IN_RAISE = "short_all_in_raise"


@dataclass(frozen=True, slots=True)
class BettingRoundPlayer:
    """Input describing one player entering a betting round."""

    player_id: PlayerId
    seat_index: SeatIndex
    stack: ChipStack
    initial_commitment: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.player_id, PlayerId):
            raise InvalidBettingParticipantError("A betting player requires a PlayerId.")
        if not isinstance(self.seat_index, SeatIndex):
            raise InvalidBettingParticipantError("A betting player requires a SeatIndex.")
        if not isinstance(self.stack, ChipStack) or self.stack.chips <= 0:
            raise InvalidBettingParticipantError("A betting player requires a positive ChipStack.")
        if (
            not isinstance(self.initial_commitment, int)
            or isinstance(self.initial_commitment, bool)
            or self.initial_commitment < 0
        ):
            raise InvalidBettingParticipantError(
                "An initial commitment requires a nonnegative integer chip count."
            )
        if self.initial_commitment > self.stack.chips:
            raise InvalidBettingParticipantError(
                "An initial commitment cannot exceed the player's starting stack."
            )


@dataclass(frozen=True, slots=True)
class BettingRoundParticipant:
    """Immutable state for one participant inside a betting round."""

    player_id: PlayerId
    seat_index: SeatIndex
    starting_stack: ChipStack
    remaining_stack: ChipStack
    committed: int
    status: BettingParticipantStatus
    last_action_wager: int | None


@dataclass(frozen=True, slots=True)
class Check:
    """Check without committing chips."""


@dataclass(frozen=True, slots=True)
class Call:
    """Call the current wager, all-in when the stack is short."""


@dataclass(frozen=True, slots=True)
class Fold:
    """Fold while preserving chips already committed."""


@dataclass(frozen=True, slots=True)
class BetTo:
    """Open betting to a total round commitment."""

    total: int

    def __post_init__(self) -> None:
        _validate_wager_total(self.total)


@dataclass(frozen=True, slots=True)
class RaiseTo:
    """Raise to a total round commitment."""

    total: int

    def __post_init__(self) -> None:
        _validate_wager_total(self.total)


type BettingAction = Check | Call | Fold | BetTo | RaiseTo


@dataclass(frozen=True, slots=True)
class CallOption:
    """The exact call currently available to a player."""

    chips: int
    total: int
    is_all_in: bool


@dataclass(frozen=True, slots=True)
class WagerBounds:
    """Full wager bounds plus an optional sole short-all-in amount."""

    minimum_full_to: int
    maximum_to: int
    short_all_in_to: int | None


@dataclass(frozen=True, slots=True)
class LegalActions:
    """Complete legal-action description for the current player."""

    player_id: PlayerId
    kinds: frozenset[ActionKind]
    amount_to_call: int
    call: CallOption | None
    bet: WagerBounds | None
    raise_to: WagerBounds | None
    raise_reopened: bool


@dataclass(frozen=True, slots=True)
class ActionResult:
    """Immutable facts produced by one successful transition."""

    player_id: PlayerId
    action_kind: ActionKind
    committed_chips: int
    wager_before: int
    wager_after: int
    classification: WagerClassification
    is_all_in: bool
    round_complete: bool


@dataclass(frozen=True, slots=True)
class BettingRoundSnapshot:
    """Complete immutable state of one betting round."""

    participants: tuple[BettingRoundParticipant, ...]
    minimum_bet: int
    minimum_raise_increment: int
    current_wager: int
    pending_players: frozenset[PlayerId]
    current_player: PlayerId | None
    complete: bool


class BettingRound:
    """Atomic aggregate implementing one no-limit betting round."""

    __slots__ = ("_snapshot",)

    def __init__(
        self,
        *,
        players: Iterable[BettingRoundPlayer],
        first_to_act: PlayerId,
        minimum_bet: int,
        initial_wager: int = 0,
    ) -> None:
        if not isinstance(minimum_bet, int) or isinstance(minimum_bet, bool) or minimum_bet <= 0:
            raise InvalidMinimumBetError("The minimum bet must be a positive integer.")
        if not isinstance(first_to_act, PlayerId):
            raise InvalidBettingRoundStateError("The first actor must be a PlayerId.")
        if (
            not isinstance(initial_wager, int)
            or isinstance(initial_wager, bool)
            or initial_wager < 0
        ):
            raise InvalidBettingRoundStateError("The initial wager must be a nonnegative integer.")

        try:
            copied_players = tuple(players)
        except TypeError:
            raise InvalidBettingRoundStateError(
                "Betting-round players must be an iterable."
            ) from None
        if len(copied_players) < 2:
            raise InvalidBettingRoundStateError(
                "A betting round requires at least two participants."
            )
        if any(not isinstance(player, BettingRoundPlayer) for player in copied_players):
            raise InvalidBettingParticipantError(
                "Betting rounds can contain only BettingRoundPlayer values."
            )

        player_ids = tuple(player.player_id for player in copied_players)
        seat_indexes = tuple(player.seat_index for player in copied_players)
        if len(set(player_ids)) != len(player_ids):
            raise DuplicateBettingPlayerError("Betting-round players must be unique.")
        if len(set(seat_indexes)) != len(seat_indexes):
            raise DuplicateBettingSeatError("Betting-round seats must be unique.")
        if first_to_act not in player_ids:
            raise PlayerNotInBettingRoundError(player_id=first_to_act.value)
        highest_initial_commitment = max(player.initial_commitment for player in copied_players)
        if initial_wager < highest_initial_commitment:
            raise InvalidBettingRoundStateError(
                "The initial wager cannot be below an initial commitment."
            )
        if initial_wager == 0 and highest_initial_commitment > 0:
            raise InvalidBettingRoundStateError(
                "A positive initial commitment requires a positive initial wager."
            )

        participants = tuple(
            sorted(
                (
                    BettingRoundParticipant(
                        player_id=player.player_id,
                        seat_index=player.seat_index,
                        starting_stack=player.stack,
                        remaining_stack=ChipStack(player.stack.chips - player.initial_commitment),
                        committed=player.initial_commitment,
                        status=(
                            BettingParticipantStatus.ALL_IN
                            if player.initial_commitment == player.stack.chips
                            else BettingParticipantStatus.ACTIVE
                        ),
                        last_action_wager=None,
                    )
                    for player in copied_players
                ),
                key=lambda participant: participant.seat_index.value,
            )
        )
        active = tuple(
            participant
            for participant in participants
            if participant.status is BettingParticipantStatus.ACTIVE
        )
        pending_players = frozenset(participant.player_id for participant in active)
        outstanding_call = any(participant.committed < initial_wager for participant in active)
        complete = not pending_players or (len(active) <= 1 and not outstanding_call)
        if not complete and first_to_act not in pending_players:
            raise InvalidBettingRoundStateError(
                "The first actor must be active when initial action is pending."
            )
        snapshot = BettingRoundSnapshot(
            participants=participants,
            minimum_bet=minimum_bet,
            minimum_raise_increment=minimum_bet,
            current_wager=initial_wager,
            pending_players=(frozenset() if complete else pending_players),
            current_player=(None if complete else first_to_act),
            complete=complete,
        )
        self._validate_snapshot(snapshot)
        self._snapshot = snapshot

    @classmethod
    def start(
        cls,
        *,
        players: Iterable[BettingRoundPlayer],
        first_to_act: PlayerId,
        minimum_bet: int,
        initial_wager: int = 0,
    ) -> Self:
        """Start a betting round with optional generic live initial commitments."""
        return cls(
            players=players,
            first_to_act=first_to_act,
            minimum_bet=minimum_bet,
            initial_wager=initial_wager,
        )

    def copy(self) -> Self:
        """Return an independent aggregate for transactional copy-on-write use."""
        copied = object.__new__(type(self))
        copied._snapshot = self._snapshot
        return copied

    @property
    def snapshot(self) -> BettingRoundSnapshot:
        return self._snapshot

    @property
    def participants(self) -> tuple[BettingRoundParticipant, ...]:
        return self._snapshot.participants

    @property
    def current_player(self) -> PlayerId | None:
        return self._snapshot.current_player

    @property
    def current_wager(self) -> int:
        return self._snapshot.current_wager

    @property
    def minimum_raise_increment(self) -> int:
        return self._snapshot.minimum_raise_increment

    @property
    def complete(self) -> bool:
        return self._snapshot.complete

    def participant(self, player_id: PlayerId) -> BettingRoundParticipant:
        """Return one participant's immutable state."""
        if not isinstance(player_id, PlayerId):
            raise InvalidBettingParticipantError("Participant lookup requires a PlayerId.")
        try:
            return self._participant_from(self._snapshot.participants, player_id)
        except LookupError:
            raise PlayerNotInBettingRoundError(player_id=player_id.value) from None

    def amount_to_call(self, player_id: PlayerId) -> int:
        """Return the chips a participant would need for a full call."""
        participant = self.participant(player_id)
        return self._snapshot.current_wager - participant.committed

    def legal_actions(self) -> LegalActions:
        """Calculate every legal action for the authoritative current player."""
        snapshot = self._snapshot
        if snapshot.complete or snapshot.current_player is None:
            raise BettingRoundCompleteError("The betting round is complete.")

        actor = self._participant_from(snapshot.participants, snapshot.current_player)
        amount_to_call = snapshot.current_wager - actor.committed
        maximum_total = actor.committed + actor.remaining_stack.chips
        kinds = {ActionKind.FOLD}
        call_option: CallOption | None = None
        bet_bounds: WagerBounds | None = None
        raise_bounds: WagerBounds | None = None

        if amount_to_call == 0:
            kinds.add(ActionKind.CHECK)
        else:
            call_chips = min(amount_to_call, actor.remaining_stack.chips)
            call_option = CallOption(
                chips=call_chips,
                total=actor.committed + call_chips,
                is_all_in=call_chips == actor.remaining_stack.chips,
            )
            kinds.add(ActionKind.CALL)

        has_active_opponent = self._has_active_opponent(snapshot, actor)
        raise_reopened = (
            snapshot.current_wager > 0
            and has_active_opponent
            and self._raise_is_reopened(snapshot, actor)
        )
        if snapshot.current_wager == 0 and maximum_total > 0 and has_active_opponent:
            bet_bounds = self._wager_bounds(
                minimum_full_to=snapshot.minimum_bet,
                maximum_to=maximum_total,
            )
            kinds.add(ActionKind.BET)
        elif (
            snapshot.current_wager > 0 and raise_reopened and maximum_total > snapshot.current_wager
        ):
            raise_bounds = self._wager_bounds(
                minimum_full_to=(snapshot.current_wager + snapshot.minimum_raise_increment),
                maximum_to=maximum_total,
            )
            kinds.add(ActionKind.RAISE)

        return LegalActions(
            player_id=actor.player_id,
            kinds=frozenset(kinds),
            amount_to_call=amount_to_call,
            call=call_option,
            bet=bet_bounds,
            raise_to=raise_bounds,
            raise_reopened=raise_reopened,
        )

    def act(self, *, player_id: PlayerId, action: BettingAction) -> ActionResult:
        """Apply one validated action and atomically replace the aggregate snapshot."""
        actor = self._checked_actor(player_id)
        before = self._snapshot

        if isinstance(action, Check):
            next_snapshot, result = self._apply_check(before, actor)
        elif isinstance(action, Call):
            next_snapshot, result = self._apply_call(before, actor)
        elif isinstance(action, BetTo):
            next_snapshot, result = self._apply_bet(before, actor, action.total)
        elif isinstance(action, RaiseTo):
            next_snapshot, result = self._apply_raise(before, actor, action.total)
        elif isinstance(action, Fold):
            next_snapshot, result = self._apply_fold(before, actor)
        else:
            raise InvalidActionTypeError("Unknown betting action.")

        self._validate_snapshot(next_snapshot)
        self._snapshot = next_snapshot
        return replace(result, round_complete=next_snapshot.complete)

    def check(self, *, player_id: PlayerId) -> ActionResult:
        return self.act(player_id=player_id, action=Check())

    def call(self, *, player_id: PlayerId) -> ActionResult:
        return self.act(player_id=player_id, action=Call())

    def bet_to(self, *, player_id: PlayerId, total: int) -> ActionResult:
        return self.act(player_id=player_id, action=BetTo(total))

    def raise_to(self, *, player_id: PlayerId, total: int) -> ActionResult:
        return self.act(player_id=player_id, action=RaiseTo(total))

    def fold(self, *, player_id: PlayerId) -> ActionResult:
        return self.act(player_id=player_id, action=Fold())

    def _checked_actor(self, player_id: PlayerId) -> BettingRoundParticipant:
        if not isinstance(player_id, PlayerId):
            raise InvalidBettingParticipantError("Betting actions require a PlayerId.")
        snapshot = self._snapshot
        if snapshot.complete or snapshot.current_player is None:
            raise BettingRoundCompleteError("The betting round is complete.")
        try:
            actor = self._participant_from(snapshot.participants, player_id)
        except LookupError:
            raise PlayerNotInBettingRoundError(player_id=player_id.value) from None
        if snapshot.current_player != player_id:
            raise OutOfTurnError(
                player_id=player_id.value,
                expected_player_id=snapshot.current_player.value,
            )
        return actor

    def _apply_check(
        self,
        snapshot: BettingRoundSnapshot,
        actor: BettingRoundParticipant,
    ) -> tuple[BettingRoundSnapshot, ActionResult]:
        amount_to_call = snapshot.current_wager - actor.committed
        if amount_to_call != 0:
            raise IllegalCheckError(f"Cannot check while owing {amount_to_call} chips.")
        updated = replace(actor, last_action_wager=snapshot.current_wager)
        next_snapshot = self._after_action(snapshot, updated, wager_increased=False)
        return next_snapshot, self._result(
            actor=updated,
            action_kind=ActionKind.CHECK,
            committed_chips=0,
            wager_before=snapshot.current_wager,
            wager_after=snapshot.current_wager,
            classification=WagerClassification.NONE,
        )

    def _apply_call(
        self,
        snapshot: BettingRoundSnapshot,
        actor: BettingRoundParticipant,
    ) -> tuple[BettingRoundSnapshot, ActionResult]:
        amount_to_call = snapshot.current_wager - actor.committed
        if amount_to_call == 0:
            raise IllegalCallError("Cannot call when no chips are owed.")
        committed_chips = min(amount_to_call, actor.remaining_stack.chips)
        remaining = actor.remaining_stack.chips - committed_chips
        updated = replace(
            actor,
            remaining_stack=ChipStack(remaining),
            committed=actor.committed + committed_chips,
            status=(
                BettingParticipantStatus.ALL_IN
                if remaining == 0
                else BettingParticipantStatus.ACTIVE
            ),
            last_action_wager=snapshot.current_wager,
        )
        next_snapshot = self._after_action(snapshot, updated, wager_increased=False)
        return next_snapshot, self._result(
            actor=updated,
            action_kind=ActionKind.CALL,
            committed_chips=committed_chips,
            wager_before=snapshot.current_wager,
            wager_after=snapshot.current_wager,
            classification=WagerClassification.NONE,
        )

    def _apply_bet(
        self,
        snapshot: BettingRoundSnapshot,
        actor: BettingRoundParticipant,
        total: int,
    ) -> tuple[BettingRoundSnapshot, ActionResult]:
        if snapshot.current_wager != 0:
            raise IllegalBetError("Cannot bet after a wager exists; use raise instead.")
        if not self._has_active_opponent(snapshot, actor):
            raise IllegalBetError("Cannot bet without an active opponent who can respond.")
        maximum_total = actor.committed + actor.remaining_stack.chips
        self._validate_requested_total(
            total=total,
            maximum_total=maximum_total,
            minimum_total=snapshot.minimum_bet,
        )
        committed_chips = total - actor.committed
        if committed_chips <= 0:
            raise InvalidWagerAmountError("A bet must increase the actor's commitment.")
        remaining = actor.remaining_stack.chips - committed_chips
        is_full = total >= snapshot.minimum_bet
        updated = replace(
            actor,
            remaining_stack=ChipStack(remaining),
            committed=total,
            status=(
                BettingParticipantStatus.ALL_IN
                if remaining == 0
                else BettingParticipantStatus.ACTIVE
            ),
            last_action_wager=total,
        )
        wager_snapshot = replace(
            snapshot,
            current_wager=total,
            minimum_raise_increment=(total if is_full else snapshot.minimum_raise_increment),
        )
        next_snapshot = self._after_action(wager_snapshot, updated, wager_increased=True)
        return next_snapshot, self._result(
            actor=updated,
            action_kind=ActionKind.BET,
            committed_chips=committed_chips,
            wager_before=snapshot.current_wager,
            wager_after=total,
            classification=(
                WagerClassification.FULL_BET if is_full else WagerClassification.SHORT_ALL_IN_BET
            ),
        )

    def _apply_raise(
        self,
        snapshot: BettingRoundSnapshot,
        actor: BettingRoundParticipant,
        total: int,
    ) -> tuple[BettingRoundSnapshot, ActionResult]:
        if snapshot.current_wager == 0:
            raise IllegalRaiseError("Cannot raise before a wager exists; use bet instead.")
        if not self._has_active_opponent(snapshot, actor):
            raise IllegalRaiseError("Cannot raise without an active opponent who can respond.")
        if not self._raise_is_reopened(snapshot, actor):
            raise RaiseNotReopenedError("Betting has not been reopened for this player.")

        maximum_total = actor.committed + actor.remaining_stack.chips
        minimum_total = snapshot.current_wager + snapshot.minimum_raise_increment
        if total <= snapshot.current_wager:
            raise InvalidWagerAmountError("A raise must increase the current wager.")
        self._validate_requested_total(
            total=total,
            maximum_total=maximum_total,
            minimum_total=minimum_total,
        )

        committed_chips = total - actor.committed
        remaining = actor.remaining_stack.chips - committed_chips
        raise_increment = total - snapshot.current_wager
        is_full = raise_increment >= snapshot.minimum_raise_increment
        updated = replace(
            actor,
            remaining_stack=ChipStack(remaining),
            committed=total,
            status=(
                BettingParticipantStatus.ALL_IN
                if remaining == 0
                else BettingParticipantStatus.ACTIVE
            ),
            last_action_wager=total,
        )
        wager_snapshot = replace(
            snapshot,
            current_wager=total,
            minimum_raise_increment=(
                raise_increment if is_full else snapshot.minimum_raise_increment
            ),
        )
        next_snapshot = self._after_action(wager_snapshot, updated, wager_increased=True)
        return next_snapshot, self._result(
            actor=updated,
            action_kind=ActionKind.RAISE,
            committed_chips=committed_chips,
            wager_before=snapshot.current_wager,
            wager_after=total,
            classification=(
                WagerClassification.FULL_RAISE
                if is_full
                else WagerClassification.SHORT_ALL_IN_RAISE
            ),
        )

    def _apply_fold(
        self,
        snapshot: BettingRoundSnapshot,
        actor: BettingRoundParticipant,
    ) -> tuple[BettingRoundSnapshot, ActionResult]:
        updated = replace(actor, status=BettingParticipantStatus.FOLDED)
        next_snapshot = self._after_action(snapshot, updated, wager_increased=False)
        return next_snapshot, self._result(
            actor=updated,
            action_kind=ActionKind.FOLD,
            committed_chips=0,
            wager_before=snapshot.current_wager,
            wager_after=snapshot.current_wager,
            classification=WagerClassification.NONE,
        )

    def _after_action(
        self,
        snapshot: BettingRoundSnapshot,
        updated_actor: BettingRoundParticipant,
        *,
        wager_increased: bool,
    ) -> BettingRoundSnapshot:
        participants = tuple(
            updated_actor if participant.player_id == updated_actor.player_id else participant
            for participant in snapshot.participants
        )
        pending = set(snapshot.pending_players)
        pending.discard(updated_actor.player_id)
        if wager_increased:
            pending.update(
                participant.player_id
                for participant in participants
                if participant.player_id != updated_actor.player_id
                and participant.status is BettingParticipantStatus.ACTIVE
                and participant.committed < snapshot.current_wager
            )
        active_ids = {
            participant.player_id
            for participant in participants
            if participant.status is BettingParticipantStatus.ACTIVE
        }
        pending.intersection_update(active_ids)

        non_folded = tuple(
            participant
            for participant in participants
            if participant.status is not BettingParticipantStatus.FOLDED
        )
        active = tuple(
            participant
            for participant in participants
            if participant.status is BettingParticipantStatus.ACTIVE
        )
        outstanding_call = any(
            participant.committed < snapshot.current_wager for participant in active
        )
        complete = (
            len(non_folded) <= 1 or not pending or (len(active) <= 1 and not outstanding_call)
        )
        if complete:
            return replace(
                snapshot,
                participants=participants,
                pending_players=frozenset(),
                current_player=None,
                complete=True,
            )

        next_player = self._next_pending_player(
            participants=participants,
            pending=frozenset(pending),
            after_seat=updated_actor.seat_index,
        )
        return replace(
            snapshot,
            participants=participants,
            pending_players=frozenset(pending),
            current_player=next_player,
        )

    @staticmethod
    def _next_pending_player(
        *,
        participants: tuple[BettingRoundParticipant, ...],
        pending: frozenset[PlayerId],
        after_seat: SeatIndex,
    ) -> PlayerId:
        ordered = tuple(
            participant
            for participant in participants
            if participant.player_id in pending
            and participant.status is BettingParticipantStatus.ACTIVE
        )
        if not ordered:
            raise InvalidBettingRoundStateError("An incomplete round requires a pending actor.")
        return next(
            (
                participant.player_id
                for participant in ordered
                if participant.seat_index.value > after_seat.value
            ),
            ordered[0].player_id,
        )

    @staticmethod
    def _participant_from(
        participants: tuple[BettingRoundParticipant, ...],
        player_id: PlayerId,
    ) -> BettingRoundParticipant:
        for participant in participants:
            if participant.player_id == player_id:
                return participant
        raise LookupError(player_id)

    @staticmethod
    def _raise_is_reopened(
        snapshot: BettingRoundSnapshot,
        participant: BettingRoundParticipant,
    ) -> bool:
        if participant.last_action_wager is None:
            return True
        return (
            snapshot.current_wager - participant.last_action_wager
            >= snapshot.minimum_raise_increment
        )

    @staticmethod
    def _has_active_opponent(
        snapshot: BettingRoundSnapshot,
        participant: BettingRoundParticipant,
    ) -> bool:
        return any(
            other.player_id != participant.player_id
            and other.status is BettingParticipantStatus.ACTIVE
            for other in snapshot.participants
        )

    @staticmethod
    def _wager_bounds(*, minimum_full_to: int, maximum_to: int) -> WagerBounds:
        return WagerBounds(
            minimum_full_to=minimum_full_to,
            maximum_to=maximum_to,
            short_all_in_to=(maximum_to if maximum_to < minimum_full_to else None),
        )

    @staticmethod
    def _validate_requested_total(
        *,
        total: int,
        maximum_total: int,
        minimum_total: int,
    ) -> None:
        if total > maximum_total:
            raise WagerExceedsStackError(
                requested_total=total,
                maximum_total=maximum_total,
            )
        if total < minimum_total and total != maximum_total:
            raise WagerBelowMinimumError(
                requested_total=total,
                minimum_total=minimum_total,
            )

    @staticmethod
    def _result(
        *,
        actor: BettingRoundParticipant,
        action_kind: ActionKind,
        committed_chips: int,
        wager_before: int,
        wager_after: int,
        classification: WagerClassification,
    ) -> ActionResult:
        return ActionResult(
            player_id=actor.player_id,
            action_kind=action_kind,
            committed_chips=committed_chips,
            wager_before=wager_before,
            wager_after=wager_after,
            classification=classification,
            is_all_in=actor.status is BettingParticipantStatus.ALL_IN,
            round_complete=False,
        )

    @staticmethod
    def _validate_snapshot(snapshot: BettingRoundSnapshot) -> None:
        participants = snapshot.participants
        if len(participants) < 2:
            raise InvalidBettingRoundStateError("A betting round requires two participants.")
        if len({participant.player_id for participant in participants}) != len(participants):
            raise InvalidBettingRoundStateError("Betting-round player IDs must be unique.")
        if len({participant.seat_index for participant in participants}) != len(participants):
            raise InvalidBettingRoundStateError("Betting-round seat indexes must be unique.")
        if tuple(sorted(participants, key=lambda item: item.seat_index.value)) != participants:
            raise InvalidBettingRoundStateError("Betting-round participants must be seat ordered.")
        if snapshot.current_wager < 0 or snapshot.minimum_raise_increment <= 0:
            raise InvalidBettingRoundStateError("Betting-round wager values are invalid.")
        if snapshot.minimum_raise_increment < snapshot.minimum_bet:
            raise InvalidBettingRoundStateError(
                "The minimum raise cannot fall below the minimum bet."
            )

        for participant in participants:
            if participant.committed < 0:
                raise InvalidBettingRoundStateError("Committed chips cannot be negative.")
            if (
                participant.starting_stack.chips
                != participant.remaining_stack.chips + participant.committed
            ):
                raise InvalidBettingRoundStateError(
                    "Participant chip accounting does not reconcile."
                )
            if (
                participant.status is BettingParticipantStatus.ACTIVE
                and participant.remaining_stack.chips == 0
            ):
                raise InvalidBettingRoundStateError("An active participant requires chips.")
            if (
                participant.status is BettingParticipantStatus.ALL_IN
                and participant.remaining_stack.chips != 0
            ):
                raise InvalidBettingRoundStateError("An all-in participant cannot retain chips.")
            if (
                participant.status is BettingParticipantStatus.FOLDED
                and participant.remaining_stack.chips == 0
            ):
                raise InvalidBettingRoundStateError(
                    "A zero-stack participant must have all-in status."
                )
            if participant.last_action_wager is not None and not (
                0 <= participant.last_action_wager <= snapshot.current_wager
            ):
                raise InvalidBettingRoundStateError("A last-action wager is outside the round.")

        if snapshot.current_wager < max(participant.committed for participant in participants):
            raise InvalidBettingRoundStateError(
                "The current wager cannot be below the high commitment."
            )
        active_ids = {
            participant.player_id
            for participant in participants
            if participant.status is BettingParticipantStatus.ACTIVE
        }
        if not snapshot.pending_players <= active_ids:
            raise InvalidBettingRoundStateError("Only active players may be pending.")
        if snapshot.complete:
            if snapshot.current_player is not None or snapshot.pending_players:
                raise InvalidBettingRoundStateError("A complete round cannot have pending action.")
        elif (
            snapshot.current_player is None
            or snapshot.current_player not in snapshot.pending_players
        ):
            raise InvalidBettingRoundStateError("An incomplete round requires a current actor.")


def _validate_wager_total(total: int) -> None:
    if not isinstance(total, int) or isinstance(total, bool) or total <= 0:
        raise InvalidWagerAmountError("A wager total must be a positive integer.")
