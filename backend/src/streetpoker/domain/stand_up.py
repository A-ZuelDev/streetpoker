"""Pure, immutable Stand-Up round rules independent of Hold'em and room transport."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Self

from streetpoker.domain.errors import (
    InvalidStandUpOutcomeError,
    InvalidStandUpStateError,
    StaleStandUpOutcomeError,
)
from streetpoker.domain.players import PlayerId
from streetpoker.domain.table import SIX_MAX_CAPACITY, SeatIndex


def _strict_int(value: object, *, minimum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _checked_seat(value: object) -> bool:
    return isinstance(value, SeatIndex) and value.value < SIX_MAX_CAPACITY


def _freeze_participants(values: Iterable[StandUpParticipant]) -> tuple[StandUpParticipant, ...]:
    try:
        participants = tuple(values)
    except TypeError:
        raise InvalidStandUpStateError("Participants must be an iterable.") from None
    if len(participants) < 2 or any(
        not isinstance(item, StandUpParticipant) for item in participants
    ):
        raise InvalidStandUpStateError("A round requires at least two valid participants.")
    if len({item.player_id for item in participants}) != len(participants):
        raise InvalidStandUpStateError("Round player identities must be unique.")
    if len({item.seat_index for item in participants}) != len(participants):
        raise InvalidStandUpStateError("Round seats must be unique.")
    return tuple(sorted(participants, key=lambda item: item.seat_index.value))


@dataclass(frozen=True, slots=True)
class StandUpParticipant:
    player_id: PlayerId
    seat_index: SeatIndex

    def __post_init__(self) -> None:
        if not isinstance(self.player_id, PlayerId) or not _checked_seat(self.seat_index):
            raise InvalidStandUpStateError("A participant requires a PlayerId and six-max seat.")


@dataclass(frozen=True, slots=True)
class StandUpHandOutcome:
    """Only the settled main-pot winners are relevant to qualification."""

    hand_number: int
    participants: tuple[PlayerId, ...]
    main_pot_winners: tuple[PlayerId, ...]
    button_seat: SeatIndex

    def __post_init__(self) -> None:
        if not _strict_int(self.hand_number, minimum=1) or not _checked_seat(self.button_seat):
            raise InvalidStandUpOutcomeError("A hand needs a positive number and six-max button.")
        try:
            participants = tuple(self.participants)
            winners = tuple(self.main_pot_winners)
        except TypeError:
            raise InvalidStandUpOutcomeError("Hand identities must be iterable.") from None
        if len(participants) < 2 or any(not isinstance(item, PlayerId) for item in participants):
            raise InvalidStandUpOutcomeError("A completed hand needs at least two players.")
        if len(set(participants)) != len(participants):
            raise InvalidStandUpOutcomeError("Hand participants must be unique.")
        if not winners or any(not isinstance(item, PlayerId) for item in winners):
            raise InvalidStandUpOutcomeError("The main pot needs at least one valid winner.")
        if len(set(winners)) != len(winners) or not set(winners) <= set(participants):
            raise InvalidStandUpOutcomeError("Main-pot winners must be unique hand participants.")
        object.__setattr__(self, "participants", participants)
        object.__setattr__(self, "main_pot_winners", winners)


@dataclass(frozen=True, slots=True)
class StandUpTransfer:
    from_player_id: PlayerId
    to_player_id: PlayerId
    chips: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.from_player_id, PlayerId)
            or not isinstance(self.to_player_id, PlayerId)
            or self.from_player_id == self.to_player_id
            or not _strict_int(self.chips, minimum=1)
        ):
            raise InvalidStandUpStateError("A transfer needs distinct players and positive chips.")


def _planned_transfers(
    participants: tuple[StandUpParticipant, ...],
    squid: PlayerId,
    penalty_per_recipient_chips: int,
    squid_available_stack: int,
    button_seat: SeatIndex,
) -> tuple[StandUpTransfer, ...]:
    recipients = sorted(
        (item for item in participants if item.player_id != squid),
        key=lambda item: (item.seat_index.value - button_seat.value - 1) % SIX_MAX_CAPACITY,
    )
    actual_total = min(squid_available_stack, penalty_per_recipient_chips * len(recipients))
    base, remainder = divmod(actual_total, len(recipients))
    return tuple(
        StandUpTransfer(squid, item.player_id, chips)
        for index, item in enumerate(recipients)
        if (chips := base + int(index < remainder)) > 0
    )


@dataclass(frozen=True, slots=True)
class StandUpResolution:
    start_hand_number: int
    hand_number: int
    participants: tuple[StandUpParticipant, ...]
    squid: PlayerId
    penalty_per_recipient_chips: int
    squid_available_stack: int
    button_seat: SeatIndex
    transfers: tuple[StandUpTransfer, ...]

    def __post_init__(self) -> None:
        if (
            not _strict_int(self.start_hand_number, minimum=1)
            or not _strict_int(self.hand_number, minimum=self.start_hand_number)
            or not _strict_int(self.penalty_per_recipient_chips, minimum=1)
            or not _strict_int(self.squid_available_stack, minimum=0)
            or not _checked_seat(self.button_seat)
        ):
            raise InvalidStandUpStateError(
                "Resolution numbers, penalty, stack, or button are invalid."
            )
        participants = _freeze_participants(self.participants)
        if not isinstance(self.squid, PlayerId) or self.squid not in {
            item.player_id for item in participants
        }:
            raise InvalidStandUpStateError("The squid must belong to the frozen cohort.")
        try:
            transfers = tuple(self.transfers)
        except TypeError:
            raise InvalidStandUpStateError("Resolution transfers must be iterable.") from None
        expected = _planned_transfers(
            participants,
            self.squid,
            self.penalty_per_recipient_chips,
            self.squid_available_stack,
            self.button_seat,
        )
        if transfers != expected:
            raise InvalidStandUpStateError("Transfers must exactly match the capped payout plan.")
        object.__setattr__(self, "participants", participants)
        object.__setattr__(self, "transfers", transfers)

    @property
    def intended_total(self) -> int:
        return self.penalty_per_recipient_chips * (len(self.participants) - 1)

    @property
    def actual_total(self) -> int:
        return sum(item.chips for item in self.transfers)

    @property
    def shortfall(self) -> int:
        return self.intended_total - self.actual_total


class StandUpCancelReason(StrEnum):
    PARTICIPANT_LEFT = "participant_left"
    PARTICIPANT_KICKED = "participant_kicked"
    PARTICIPANT_VACATED_SEAT = "participant_vacated_seat"
    PARTICIPANT_BUSTED = "participant_busted"
    DISABLED = "disabled"
    ROOM_CLOSED = "room_closed"


@dataclass(frozen=True, slots=True)
class StandUpCancellation:
    start_hand_number: int
    last_processed_hand_number: int
    participants: tuple[StandUpParticipant, ...]
    cleared_player_ids: frozenset[PlayerId]
    reason: StandUpCancelReason

    def __post_init__(self) -> None:
        _validate_round_state(
            self.start_hand_number,
            self.last_processed_hand_number,
            self.participants,
            self.cleared_player_ids,
        )
        if not isinstance(self.reason, StandUpCancelReason):
            raise InvalidStandUpStateError("Cancellation requires a defined reason.")
        object.__setattr__(self, "participants", _freeze_participants(self.participants))
        object.__setattr__(self, "cleared_player_ids", frozenset(self.cleared_player_ids))


def _validate_round_state(
    start_hand_number: int,
    last_processed_hand_number: int,
    participants: Iterable[StandUpParticipant],
    cleared_player_ids: Iterable[PlayerId],
) -> None:
    if not _strict_int(start_hand_number, minimum=1) or not _strict_int(
        last_processed_hand_number, minimum=start_hand_number - 1
    ):
        raise InvalidStandUpStateError("Round hand numbers are invalid.")
    frozen_participants = _freeze_participants(participants)
    try:
        cleared = tuple(cleared_player_ids)
    except TypeError:
        raise InvalidStandUpStateError("Cleared identities must be iterable.") from None
    if any(not isinstance(item, PlayerId) for item in cleared) or len(set(cleared)) != len(cleared):
        raise InvalidStandUpStateError("Cleared identities must be unique PlayerIds.")
    cohort = {item.player_id for item in frozen_participants}
    if not set(cleared) <= cohort or len(cohort - set(cleared)) < 2:
        raise InvalidStandUpStateError("An active round needs at least two at-risk cohort players.")
    processed_count = last_processed_hand_number - start_hand_number + 1
    if len(cleared) > processed_count:
        raise InvalidStandUpStateError("More players cleared than completed hands allow.")


@dataclass(frozen=True, slots=True)
class StandUpRound:
    start_hand_number: int
    participants: tuple[StandUpParticipant, ...]
    penalty_per_recipient_chips: int
    last_processed_hand_number: int
    cleared_player_ids: frozenset[PlayerId]

    def __post_init__(self) -> None:
        _validate_round_state(
            self.start_hand_number,
            self.last_processed_hand_number,
            self.participants,
            self.cleared_player_ids,
        )
        if not _strict_int(self.penalty_per_recipient_chips, minimum=1):
            raise InvalidStandUpStateError("The frozen penalty must be positive whole chips.")
        object.__setattr__(self, "participants", _freeze_participants(self.participants))
        object.__setattr__(self, "cleared_player_ids", frozenset(self.cleared_player_ids))

    @classmethod
    def start(
        cls,
        *,
        start_hand_number: int,
        participants: Iterable[StandUpParticipant],
        penalty_per_recipient_chips: int,
    ) -> Self:
        if not _strict_int(start_hand_number, minimum=1):
            raise InvalidStandUpStateError("A round needs a positive starting hand number.")
        return cls(
            start_hand_number=start_hand_number,
            participants=_freeze_participants(participants),
            penalty_per_recipient_chips=penalty_per_recipient_chips,
            last_processed_hand_number=start_hand_number - 1,
            cleared_player_ids=frozenset(),
        )

    @property
    def at_risk_player_ids(self) -> frozenset[PlayerId]:
        return frozenset(item.player_id for item in self.participants) - self.cleared_player_ids

    def apply_hand(
        self,
        outcome: StandUpHandOutcome,
        *,
        squid_available_stack: int | None = None,
    ) -> StandUpRound | StandUpResolution:
        if not isinstance(outcome, StandUpHandOutcome):
            raise InvalidStandUpOutcomeError("A round requires a completed-hand outcome.")
        if outcome.hand_number <= self.last_processed_hand_number:
            raise StaleStandUpOutcomeError("The hand has already been processed or is stale.")
        if outcome.hand_number != self.last_processed_hand_number + 1:
            raise InvalidStandUpOutcomeError("Completed hands must be processed in order.")
        if not {item.player_id for item in self.participants} <= set(outcome.participants):
            raise InvalidStandUpOutcomeError("The completed hand omits a frozen participant.")
        if squid_available_stack is not None and not _strict_int(squid_available_stack, minimum=0):
            raise InvalidStandUpOutcomeError("The squid stack must be nonnegative whole chips.")
        cleared = self.cleared_player_ids
        if len(outcome.main_pot_winners) == 1:
            winner = outcome.main_pot_winners[0]
            if winner in self.at_risk_player_ids:
                cleared = cleared | {winner}
        remaining = self.at_risk_player_ids - cleared
        if len(remaining) > 1:
            return replace(
                self,
                last_processed_hand_number=outcome.hand_number,
                cleared_player_ids=cleared,
            )
        if len(remaining) != 1:
            raise InvalidStandUpStateError("A valid hand cannot clear every at-risk player.")
        if squid_available_stack is None:
            raise InvalidStandUpOutcomeError("A resolving hand requires the squid's settled stack.")
        squid = next(iter(remaining))
        participants = self.participants
        transfers = _planned_transfers(
            participants,
            squid,
            self.penalty_per_recipient_chips,
            squid_available_stack,
            outcome.button_seat,
        )
        return StandUpResolution(
            start_hand_number=self.start_hand_number,
            hand_number=outcome.hand_number,
            participants=participants,
            squid=squid,
            penalty_per_recipient_chips=self.penalty_per_recipient_chips,
            squid_available_stack=squid_available_stack,
            button_seat=outcome.button_seat,
            transfers=transfers,
        )

    def cancel(self, reason: StandUpCancelReason) -> StandUpCancellation:
        return StandUpCancellation(
            start_hand_number=self.start_hand_number,
            last_processed_hand_number=self.last_processed_hand_number,
            participants=self.participants,
            cleared_player_ids=self.cleared_player_ids,
            reason=reason,
        )
