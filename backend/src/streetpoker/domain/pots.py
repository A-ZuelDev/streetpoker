"""Pure, deterministic construction of contestable pots from cumulative commitments."""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from streetpoker.domain.errors import (
    DuplicatePotContributorError,
    InvalidCommittedChipsError,
    InvalidConstructedPotError,
    InvalidPotConstructionInputError,
    InvalidPotContributionError,
    NoEligiblePotParticipantError,
    UnawardablePotError,
)
from streetpoker.domain.players import PlayerId


class PotEligibilityStatus(StrEnum):
    """Whether a contributor may win pots; chips behind are deliberately irrelevant."""

    LIVE = "live"
    FOLDED = "folded"


@dataclass(frozen=True, slots=True)
class PotContribution:
    """One participant's authoritative cumulative commitment and fold status."""

    player_id: PlayerId
    committed: int
    status: PotEligibilityStatus

    def __post_init__(self) -> None:
        if not isinstance(self.player_id, PlayerId):
            raise InvalidPotContributionError("A pot contribution requires a PlayerId.")
        if not isinstance(self.committed, int) or isinstance(self.committed, bool):
            raise InvalidCommittedChipsError(
                "A cumulative commitment requires an integer chip count."
            )
        if self.committed < 0:
            raise InvalidCommittedChipsError("A cumulative commitment cannot be negative.")
        if not isinstance(self.status, PotEligibilityStatus):
            raise InvalidPotContributionError("A pot contribution requires a PotEligibilityStatus.")


@dataclass(frozen=True, slots=True)
class ContributionTier:
    """A cumulative contribution interval represented as (lower, upper]."""

    lower_threshold: int
    upper_threshold: int

    def __post_init__(self) -> None:
        if not _is_strict_int(self.lower_threshold) or not _is_strict_int(self.upper_threshold):
            raise InvalidConstructedPotError("Contribution thresholds must be integers.")
        if self.lower_threshold < 0:
            raise InvalidConstructedPotError("A contribution tier cannot start below zero.")
        if self.upper_threshold <= self.lower_threshold:
            raise InvalidConstructedPotError("A contribution tier requires positive width.")

    @property
    def width(self) -> int:
        return self.upper_threshold - self.lower_threshold


@dataclass(frozen=True, slots=True)
class Pot:
    """One contestable contribution tier and its funding and eligibility sets."""

    tier: ContributionTier
    amount: int
    contributors: frozenset[PlayerId]
    eligible_players: frozenset[PlayerId]

    def __post_init__(self) -> None:
        if not isinstance(self.tier, ContributionTier):
            raise InvalidConstructedPotError("A pot requires a ContributionTier.")
        if not _is_strict_int(self.amount) or self.amount <= 0:
            raise InvalidConstructedPotError("A pot amount must be a positive integer.")
        if not isinstance(self.contributors, frozenset) or any(
            not isinstance(player_id, PlayerId) for player_id in self.contributors
        ):
            raise InvalidConstructedPotError("Pot contributors must be a frozenset of PlayerId.")
        if len(self.contributors) < 2:
            raise InvalidConstructedPotError("A pot requires at least two contributors.")
        if not isinstance(self.eligible_players, frozenset) or any(
            not isinstance(player_id, PlayerId) for player_id in self.eligible_players
        ):
            raise InvalidConstructedPotError("Pot eligibility must be a frozenset of PlayerId.")
        if not self.eligible_players:
            raise InvalidConstructedPotError("A pot requires at least one eligible player.")
        if not self.eligible_players <= self.contributors:
            raise InvalidConstructedPotError("Eligible players must be pot contributors.")
        if self.amount != self.tier.width * len(self.contributors):
            raise InvalidConstructedPotError(
                "A pot amount must equal its tier width times its contributor count."
            )


@dataclass(frozen=True, slots=True)
class UncalledExcess:
    """A unique maximum contribution layer that was not matched by another player."""

    player_id: PlayerId
    chips: int

    def __post_init__(self) -> None:
        if not isinstance(self.player_id, PlayerId):
            raise InvalidConstructedPotError("Uncalled excess requires a PlayerId.")
        if not _is_strict_int(self.chips) or self.chips <= 0:
            raise InvalidConstructedPotError("Uncalled excess must be a positive integer.")


@dataclass(frozen=True, slots=True)
class PotConstructionResult:
    """Canonical immutable pots, refund, and the authoritative source contributions."""

    contributions: tuple[PotContribution, ...]
    pots: tuple[Pot, ...]
    uncalled_excess: UncalledExcess | None

    def __post_init__(self) -> None:
        _validate_result(self)

    @property
    def main_pot(self) -> Pot | None:
        return self.pots[0] if self.pots else None

    @property
    def side_pots(self) -> tuple[Pot, ...]:
        return self.pots[1:]

    @property
    def total_pot_chips(self) -> int:
        return sum(pot.amount for pot in self.pots)

    @property
    def total_returned_chips(self) -> int:
        return 0 if self.uncalled_excess is None else self.uncalled_excess.chips


def construct_pots(
    contributions: Iterable[PotContribution],
) -> PotConstructionResult:
    """Construct ordered contestable pots and any unique-highest uncalled excess."""
    try:
        copied_contributions = tuple(contributions)
    except TypeError:
        raise InvalidPotConstructionInputError("Pot contributions must be an iterable.") from None

    if len(copied_contributions) < 2:
        raise InvalidPotConstructionInputError(
            "Pot construction requires at least two participants."
        )
    if any(not isinstance(contribution, PotContribution) for contribution in copied_contributions):
        raise InvalidPotContributionError("Pot construction accepts only PotContribution values.")

    player_ids = tuple(contribution.player_id for contribution in copied_contributions)
    if len(set(player_ids)) != len(player_ids):
        raise DuplicatePotContributorError("Pot-construction players must be unique.")
    if all(
        contribution.status is PotEligibilityStatus.FOLDED for contribution in copied_contributions
    ):
        raise NoEligiblePotParticipantError(
            "Pot construction requires at least one live participant."
        )

    canonical = tuple(
        sorted(copied_contributions, key=lambda contribution: contribution.player_id.value)
    )
    descending_commitments = sorted(
        (contribution.committed for contribution in canonical), reverse=True
    )
    highest = descending_commitments[0]
    second_highest = descending_commitments[1]
    unique_maximum = descending_commitments.count(highest) == 1
    contestable_cap = second_highest if unique_maximum else highest

    excess: UncalledExcess | None = None
    if unique_maximum and highest > second_highest:
        maximum_contributor = next(
            contribution for contribution in canonical if contribution.committed == highest
        )
        excess = UncalledExcess(
            player_id=maximum_contributor.player_id,
            chips=highest - second_highest,
        )

    effective = {
        contribution.player_id: min(contribution.committed, contestable_cap)
        for contribution in canonical
    }
    thresholds = sorted({amount for amount in effective.values() if amount > 0})
    pots: list[Pot] = []
    previous_threshold = 0

    for threshold in thresholds:
        contributors = frozenset(
            contribution.player_id
            for contribution in canonical
            if effective[contribution.player_id] >= threshold
        )
        if len(contributors) < 2:
            raise InvalidConstructedPotError(
                "A contestable contribution tier requires at least two contributors."
            )
        eligible_players = frozenset(
            contribution.player_id
            for contribution in canonical
            if contribution.player_id in contributors
            and contribution.status is PotEligibilityStatus.LIVE
        )
        if not eligible_players:
            raise UnawardablePotError(
                lower_threshold=previous_threshold,
                upper_threshold=threshold,
            )
        tier = ContributionTier(
            lower_threshold=previous_threshold,
            upper_threshold=threshold,
        )
        pots.append(
            Pot(
                tier=tier,
                amount=tier.width * len(contributors),
                contributors=contributors,
                eligible_players=eligible_players,
            )
        )
        previous_threshold = threshold

    return PotConstructionResult(
        contributions=canonical,
        pots=tuple(pots),
        uncalled_excess=excess,
    )


def _validate_result(result: PotConstructionResult) -> None:
    contributions = result.contributions
    if not isinstance(contributions, tuple) or len(contributions) < 2:
        raise InvalidConstructedPotError(
            "A pot-construction result requires at least two contributions."
        )
    if any(not isinstance(item, PotContribution) for item in contributions):
        raise InvalidConstructedPotError(
            "Result contributions must contain only PotContribution values."
        )
    if tuple(sorted(contributions, key=lambda item: item.player_id.value)) != contributions:
        raise InvalidConstructedPotError("Result contributions must be canonically ordered.")
    if len({item.player_id for item in contributions}) != len(contributions):
        raise InvalidConstructedPotError("Result contributions must have unique players.")
    if not isinstance(result.pots, tuple) or any(not isinstance(pot, Pot) for pot in result.pots):
        raise InvalidConstructedPotError("Result pots must be a tuple of Pot values.")
    if result.uncalled_excess is not None and not isinstance(
        result.uncalled_excess, UncalledExcess
    ):
        raise InvalidConstructedPotError("Result uncalled excess must be UncalledExcess or None.")

    contribution_by_id = {item.player_id: item for item in contributions}
    previous_threshold = 0
    for pot in result.pots:
        if pot.tier.lower_threshold != previous_threshold:
            raise InvalidConstructedPotError(
                "Result pot tiers must be contiguous and strictly increasing."
            )
        expected_contributors = frozenset(
            item.player_id for item in contributions if item.committed >= pot.tier.upper_threshold
        )
        if pot.contributors != expected_contributors:
            raise InvalidConstructedPotError(
                "Pot contributors must fund the complete contribution tier."
            )
        expected_eligible = frozenset(
            player_id
            for player_id in expected_contributors
            if contribution_by_id[player_id].status is PotEligibilityStatus.LIVE
        )
        if pot.eligible_players != expected_eligible:
            raise InvalidConstructedPotError(
                "Pot eligibility must equal its live contributor subset."
            )
        previous_threshold = pot.tier.upper_threshold

    descending = sorted((item.committed for item in contributions), reverse=True)
    highest = descending[0]
    second_highest = descending[1]
    maximum_players = tuple(item for item in contributions if item.committed == highest)
    expected_excess = highest - second_highest if len(maximum_players) == 1 else 0
    if expected_excess == 0:
        if result.uncalled_excess is not None:
            raise InvalidConstructedPotError("A tied maximum cannot have uncalled excess.")
    elif result.uncalled_excess != UncalledExcess(
        player_id=maximum_players[0].player_id,
        chips=expected_excess,
    ):
        raise InvalidConstructedPotError(
            "Uncalled excess must match the unique maximum contribution."
        )

    returned_by_id = (
        {}
        if result.uncalled_excess is None
        else {result.uncalled_excess.player_id: result.uncalled_excess.chips}
    )
    for contribution in contributions:
        represented_in_pots = sum(
            pot.tier.width for pot in result.pots if contribution.player_id in pot.contributors
        )
        if contribution.committed != represented_in_pots + returned_by_id.get(
            contribution.player_id, 0
        ):
            raise InvalidConstructedPotError(
                f"Contribution for player {contribution.player_id.value!r} does not reconcile."
            )

    if sum(item.committed for item in contributions) != (
        result.total_pot_chips + result.total_returned_chips
    ):
        raise InvalidConstructedPotError("Constructed pots do not globally reconcile.")


def _is_strict_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
