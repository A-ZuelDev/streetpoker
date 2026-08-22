from collections.abc import Iterator
from dataclasses import FrozenInstanceError

import pytest
from hypothesis import given
from hypothesis import strategies as st

from streetpoker.domain import (
    ContributionTier,
    DuplicatePotContributorError,
    InvalidCommittedChipsError,
    InvalidConstructedPotError,
    InvalidPotConstructionInputError,
    InvalidPotContributionError,
    NoEligiblePotParticipantError,
    PlayerId,
    Pot,
    PotConstructionResult,
    PotContribution,
    PotEligibilityStatus,
    UnawardablePotError,
    UncalledExcess,
    construct_pots,
)

LIVE = PotEligibilityStatus.LIVE
FOLDED = PotEligibilityStatus.FOLDED

type InputRow = tuple[str, int, PotEligibilityStatus]
type ExpectedPot = tuple[int, int, int, frozenset[str], frozenset[str]]


def contribution(
    name: str,
    committed: int,
    status: PotEligibilityStatus = LIVE,
) -> PotContribution:
    return PotContribution(PlayerId(name), committed, status)


def player_ids(*names: str) -> frozenset[PlayerId]:
    return frozenset(PlayerId(name) for name in names)


def assert_result(
    rows: tuple[InputRow, ...],
    expected_pots: tuple[ExpectedPot, ...],
    expected_refund: tuple[str, int] | None = None,
) -> PotConstructionResult:
    result = construct_pots(contribution(*row) for row in rows)

    assert len(result.pots) == len(expected_pots)
    for pot, (lower, upper, amount, contributors, eligible) in zip(
        result.pots, expected_pots, strict=True
    ):
        assert pot == Pot(
            tier=ContributionTier(lower, upper),
            amount=amount,
            contributors=frozenset(PlayerId(name) for name in contributors),
            eligible_players=frozenset(PlayerId(name) for name in eligible),
        )

    assert result.uncalled_excess == (
        None
        if expected_refund is None
        else UncalledExcess(PlayerId(expected_refund[0]), expected_refund[1])
    )
    assert result.main_pot == (result.pots[0] if result.pots else None)
    assert result.side_pots == result.pots[1:]
    assert sum(row[1] for row in rows) == (result.total_pot_chips + result.total_returned_chips)
    return result


@pytest.mark.parametrize(
    ("rows", "expected_pots", "expected_refund"),
    [
        (
            (("A", 100, LIVE), ("B", 100, LIVE)),
            ((0, 100, 200, frozenset("AB"), frozenset("AB")),),
            None,
        ),
        (
            (("A", 100, LIVE), ("B", 100, LIVE), ("C", 100, LIVE)),
            ((0, 100, 300, frozenset("ABC"), frozenset("ABC")),),
            None,
        ),
        (
            (("A", 50, LIVE), ("B", 200, LIVE), ("C", 200, LIVE)),
            (
                (0, 50, 150, frozenset("ABC"), frozenset("ABC")),
                (50, 200, 300, frozenset("BC"), frozenset("BC")),
            ),
            None,
        ),
        (
            (
                ("A", 50, LIVE),
                ("B", 120, LIVE),
                ("C", 300, LIVE),
                ("D", 300, LIVE),
            ),
            (
                (0, 50, 200, frozenset("ABCD"), frozenset("ABCD")),
                (50, 120, 210, frozenset("BCD"), frozenset("BCD")),
                (120, 300, 360, frozenset("CD"), frozenset("CD")),
            ),
            None,
        ),
        (
            (
                ("A", 40, LIVE),
                ("B", 100, LIVE),
                ("C", 175, LIVE),
                ("D", 250, LIVE),
                ("E", 250, LIVE),
            ),
            (
                (0, 40, 200, frozenset("ABCDE"), frozenset("ABCDE")),
                (40, 100, 240, frozenset("BCDE"), frozenset("BCDE")),
                (100, 175, 225, frozenset("CDE"), frozenset("CDE")),
                (175, 250, 150, frozenset("DE"), frozenset("DE")),
            ),
            None,
        ),
        (
            (("A", 100, FOLDED), ("B", 100, LIVE), ("C", 100, LIVE)),
            ((0, 100, 300, frozenset("ABC"), frozenset("BC")),),
            None,
        ),
        (
            (("A", 200, FOLDED), ("B", 150, LIVE), ("C", 100, LIVE)),
            (
                (0, 100, 300, frozenset("ABC"), frozenset("BC")),
                (100, 150, 100, frozenset("AB"), frozenset("B")),
            ),
            ("A", 50),
        ),
        (
            (("A", 400, LIVE), ("B", 175, LIVE), ("C", 100, LIVE)),
            (
                (0, 100, 300, frozenset("ABC"), frozenset("ABC")),
                (100, 175, 150, frozenset("AB"), frozenset("AB")),
            ),
            ("A", 225),
        ),
        (
            (("A", 300, LIVE), ("B", 180, FOLDED), ("C", 100, LIVE)),
            (
                (0, 100, 300, frozenset("ABC"), frozenset("AC")),
                (100, 180, 160, frozenset("AB"), frozenset("A")),
            ),
            ("A", 120),
        ),
        (
            (
                ("A", 50, LIVE),
                ("B", 100, FOLDED),
                ("C", 175, LIVE),
                ("D", 175, LIVE),
                ("E", 100, FOLDED),
            ),
            (
                (0, 50, 250, frozenset("ABCDE"), frozenset("ACD")),
                (50, 100, 200, frozenset("BCDE"), frozenset("CD")),
                (100, 175, 150, frozenset("CD"), frozenset("CD")),
            ),
            None,
        ),
        (
            (("A", 100, LIVE), ("B", 250, LIVE)),
            ((0, 100, 200, frozenset("AB"), frozenset("AB")),),
            ("B", 150),
        ),
        (
            (
                ("A", 100, LIVE),
                ("B", 250, LIVE),
                ("C", 600, LIVE),
                ("D", 600, LIVE),
            ),
            (
                (0, 100, 400, frozenset("ABCD"), frozenset("ABCD")),
                (100, 250, 450, frozenset("BCD"), frozenset("BCD")),
                (250, 600, 700, frozenset("CD"), frozenset("CD")),
            ),
            None,
        ),
        (
            (
                ("A", 50, LIVE),
                ("B", 100, LIVE),
                ("C", 175, LIVE),
                ("D", 400, LIVE),
                ("E", 400, LIVE),
            ),
            (
                (0, 50, 250, frozenset("ABCDE"), frozenset("ABCDE")),
                (50, 100, 200, frozenset("BCDE"), frozenset("BCDE")),
                (100, 175, 225, frozenset("CDE"), frozenset("CDE")),
                (175, 400, 450, frozenset("DE"), frozenset("DE")),
            ),
            None,
        ),
        (
            (
                ("A", 100, LIVE),
                ("B", 250, FOLDED),
                ("C", 600, LIVE),
                ("D", 250, LIVE),
            ),
            (
                (0, 100, 400, frozenset("ABCD"), frozenset("ACD")),
                (100, 250, 450, frozenset("BCD"), frozenset("CD")),
            ),
            ("C", 350),
        ),
        (
            (("A", 0, LIVE), ("B", 0, LIVE), ("C", 0, LIVE)),
            (),
            None,
        ),
    ],
    ids=[
        "two-equal",
        "three-equal",
        "short-versus-two-covering",
        "two-distinct-short-levels",
        "four-contestable-levels",
        "folded-contributor",
        "folded-unique-maximum",
        "one-player-covers-all",
        "uncalled-raise-with-fold",
        "multiple-folded-and-live",
        "unequal-heads-up",
        "100-250-600-600",
        "50-100-175-400-400",
        "one-funds-highest-layer",
        "no-commitments",
    ],
)
def test_approved_contribution_examples(
    rows: tuple[InputRow, ...],
    expected_pots: tuple[ExpectedPot, ...],
    expected_refund: tuple[str, int] | None,
) -> None:
    assert_result(rows, expected_pots, expected_refund)


@pytest.mark.parametrize(
    ("commitments", "expected_pots", "expected_refund"),
    [
        ((100, 100), ((0, 100, 200, frozenset("AB"), frozenset("AB")),), None),
        ((100, 101), ((0, 100, 200, frozenset("AB"), frozenset("AB")),), ("B", 1)),
        ((100, 250), ((0, 100, 200, frozenset("AB"), frozenset("AB")),), ("B", 150)),
        (
            (100, 250, 250),
            (
                (0, 100, 300, frozenset("ABC"), frozenset("ABC")),
                (100, 250, 300, frozenset("BC"), frozenset("BC")),
            ),
            None,
        ),
        ((0, 0, 1), (), ("C", 1)),
        ((0, 0, 100), (), ("C", 100)),
        (
            (100, 250, 600),
            (
                (0, 100, 300, frozenset("ABC"), frozenset("ABC")),
                (100, 250, 300, frozenset("BC"), frozenset("BC")),
            ),
            ("C", 350),
        ),
        (
            (100, 250, 599, 600),
            (
                (0, 100, 400, frozenset("ABCD"), frozenset("ABCD")),
                (100, 250, 450, frozenset("BCD"), frozenset("BCD")),
                (250, 599, 698, frozenset("CD"), frozenset("CD")),
            ),
            ("D", 1),
        ),
        (
            (100, 250, 600, 600),
            (
                (0, 100, 400, frozenset("ABCD"), frozenset("ABCD")),
                (100, 250, 450, frozenset("BCD"), frozenset("BCD")),
                (250, 600, 700, frozenset("CD"), frozenset("CD")),
            ),
            None,
        ),
    ],
)
def test_unique_maximum_boundaries(
    commitments: tuple[int, ...],
    expected_pots: tuple[ExpectedPot, ...],
    expected_refund: tuple[str, int] | None,
) -> None:
    rows = tuple(
        (chr(ord("A") + index), committed, LIVE) for index, committed in enumerate(commitments)
    )
    result = assert_result(rows, expected_pots, expected_refund)

    assert tuple(item.committed for item in result.contributions) == commitments


def test_authoritative_commitment_is_preserved_when_excess_is_returned() -> None:
    result = construct_pots(
        [contribution("A", 600), contribution("B", 250), contribution("C", 100)]
    )

    assert result.contributions[0].committed == 600
    assert result.uncalled_excess == UncalledExcess(PlayerId("A"), 350)
    assert sum(pot.tier.width for pot in result.pots if PlayerId("A") in pot.contributors) == 250


def test_input_order_is_canonicalized_without_mutating_the_caller_collection() -> None:
    inputs = [contribution("C", 100), contribution("A", 300), contribution("B", 200)]
    before = inputs.copy()

    first = construct_pots(inputs)
    second = construct_pots(reversed(inputs))

    assert inputs == before
    assert first == second
    assert tuple(item.player_id.value for item in first.contributions) == ("A", "B", "C")


def test_generator_input_is_materialized_once() -> None:
    yielded: list[str] = []

    def generated() -> Iterator[PotContribution]:
        for item in (contribution("A", 100), contribution("B", 100)):
            yielded.append(item.player_id.value)
            yield item

    result = construct_pots(generated())

    assert result.total_pot_chips == 200
    assert yielded == ["A", "B"]


def test_folded_players_fund_a_single_eligible_player_without_causing_a_refund() -> None:
    result = construct_pots(
        [
            contribution("A", 200, FOLDED),
            contribution("B", 200, LIVE),
            contribution("C", 100, LIVE),
        ]
    )

    assert result.side_pots[0].contributors == player_ids("A", "B")
    assert result.side_pots[0].eligible_players == player_ids("B")
    assert result.uncalled_excess is None


def test_funded_tier_with_no_live_contributor_is_rejected_atomically() -> None:
    inputs = (
        contribution("A", 200, FOLDED),
        contribution("B", 200, FOLDED),
        contribution("C", 100, LIVE),
    )

    with pytest.raises(UnawardablePotError) as error:
        construct_pots(inputs)

    assert error.value.lower_threshold == 100
    assert error.value.upper_threshold == 200
    assert tuple(item.committed for item in inputs) == (200, 200, 100)


def test_pot_values_and_results_are_immutable() -> None:
    result = construct_pots([contribution("A", 100), contribution("B", 100)])

    with pytest.raises(FrozenInstanceError):
        result.pots = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.main_pot.amount = 0  # type: ignore[union-attr,misc]
    with pytest.raises(FrozenInstanceError):
        result.contributions[0].committed = 0  # type: ignore[misc]


@pytest.mark.parametrize("committed", [-1, True, False, 1.5, "1", None, object()])
def test_invalid_committed_values_raise_typed_errors(committed: object) -> None:
    with pytest.raises(InvalidCommittedChipsError):
        PotContribution(PlayerId("A"), committed, LIVE)  # type: ignore[arg-type]


def test_zero_commitment_is_valid_for_live_and_folded_participants() -> None:
    assert contribution("A", 0, LIVE).committed == 0
    assert contribution("B", 0, FOLDED).committed == 0


def test_pot_eligibility_status_contains_only_live_and_folded() -> None:
    assert list(PotEligibilityStatus) == [LIVE, FOLDED]


@pytest.mark.parametrize(
    ("player_id", "status"),
    [("A", LIVE), (PlayerId("A"), "live"), (PlayerId("A"), object())],
)
def test_contribution_requires_typed_components(player_id: object, status: object) -> None:
    with pytest.raises(InvalidPotContributionError):
        PotContribution(player_id, 10, status)  # type: ignore[arg-type]


def test_collection_validation_is_typed() -> None:
    with pytest.raises(InvalidPotConstructionInputError):
        construct_pots(None)  # type: ignore[arg-type]
    with pytest.raises(InvalidPotConstructionInputError):
        construct_pots([])
    with pytest.raises(InvalidPotConstructionInputError):
        construct_pots([contribution("A", 10)])
    with pytest.raises(InvalidPotContributionError):
        construct_pots([contribution("A", 10), object()])  # type: ignore[list-item]


def test_duplicate_players_and_all_folded_inputs_are_typed_failures() -> None:
    with pytest.raises(DuplicatePotContributorError):
        construct_pots([contribution("A", 10), contribution("A", 20)])
    with pytest.raises(NoEligiblePotParticipantError):
        construct_pots([contribution("A", 10, FOLDED), contribution("B", 10, FOLDED)])


def test_invalid_directly_constructed_domain_values_are_rejected() -> None:
    with pytest.raises(InvalidConstructedPotError):
        ContributionTier(10, 10)
    with pytest.raises(InvalidConstructedPotError):
        ContributionTier(True, 10)  # type: ignore[arg-type]
    with pytest.raises(InvalidConstructedPotError):
        UncalledExcess(PlayerId("A"), 0)
    with pytest.raises(InvalidConstructedPotError):
        Pot(ContributionTier(0, 10), 10, player_ids("A"), player_ids("A"))
    with pytest.raises(InvalidConstructedPotError):
        Pot(
            ContributionTier(0, 10),
            20,
            player_ids("A", "B"),
            frozenset(),
        )
    with pytest.raises(InvalidConstructedPotError):
        PotConstructionResult(
            contributions=(contribution("A", 10), contribution("B", 10)),
            pots=(),
            uncalled_excess=None,
        )


@st.composite
def valid_contribution_sets(draw: st.DrawFn) -> tuple[PotContribution, ...]:
    commitments = draw(st.lists(st.integers(min_value=0, max_value=10_000), min_size=2, max_size=6))
    folded = draw(st.lists(st.booleans(), min_size=len(commitments), max_size=len(commitments)))
    live_maximum_index = commitments.index(max(commitments))
    folded[live_maximum_index] = False
    return tuple(
        contribution(
            f"P{index}",
            committed,
            FOLDED if folded[index] else LIVE,
        )
        for index, committed in enumerate(commitments)
    )


def arithmetic_signature(
    result: PotConstructionResult,
) -> tuple[tuple[ContributionTier, int, frozenset[PlayerId]], ...]:
    return tuple((pot.tier, pot.amount, pot.contributors) for pot in result.pots)


@given(contributions=valid_contribution_sets())
def test_generated_results_preserve_all_pot_and_reconciliation_invariants(
    contributions: tuple[PotContribution, ...],
) -> None:
    result = construct_pots(contributions)
    status_by_id = {item.player_id: item.status for item in contributions}
    returned_by_id = (
        {}
        if result.uncalled_excess is None
        else {result.uncalled_excess.player_id: result.uncalled_excess.chips}
    )

    assert sum(item.committed for item in contributions) == (
        result.total_pot_chips + result.total_returned_chips
    )
    assert [pot.tier.upper_threshold for pot in result.pots] == sorted(
        pot.tier.upper_threshold for pot in result.pots
    )
    assert all(
        lower.tier.upper_threshold < upper.tier.upper_threshold
        for lower, upper in zip(result.pots, result.pots[1:], strict=False)
    )
    assert len(result.pots) <= len(contributions) - 1

    for pot in result.pots:
        assert pot.tier.width > 0
        assert pot.amount > 0
        assert len(pot.contributors) >= 2
        assert pot.amount == pot.tier.width * len(pot.contributors)
        assert pot.eligible_players
        assert pot.eligible_players == frozenset(
            player_id for player_id in pot.contributors if status_by_id[player_id] is LIVE
        )
        assert all(
            next(item.committed for item in contributions if item.player_id == player_id)
            >= pot.tier.upper_threshold
            for player_id in pot.eligible_players
        )

    for contribution_item in contributions:
        represented = sum(
            pot.tier.width for pot in result.pots if contribution_item.player_id in pot.contributors
        )
        assert contribution_item.committed == represented + returned_by_id.get(
            contribution_item.player_id, 0
        )


@given(contributions=valid_contribution_sets())
def test_generated_unique_and_tied_maxima_have_exact_refund_behavior(
    contributions: tuple[PotContribution, ...],
) -> None:
    result = construct_pots(contributions)
    ordered = sorted(contributions, key=lambda item: item.committed, reverse=True)
    highest = ordered[0].committed
    second_highest = ordered[1].committed
    maximum_players = [item for item in ordered if item.committed == highest]

    if len(maximum_players) == 1 and highest > second_highest:
        assert result.uncalled_excess == UncalledExcess(
            maximum_players[0].player_id,
            highest - second_highest,
        )
    else:
        assert result.uncalled_excess is None


@given(contributions=valid_contribution_sets(), data=st.data())
def test_generated_input_permutations_and_repeated_construction_are_deterministic(
    contributions: tuple[PotContribution, ...],
    data: st.DataObject,
) -> None:
    permutation = tuple(data.draw(st.permutations(contributions)))

    expected = construct_pots(contributions)

    assert construct_pots(permutation) == expected
    assert construct_pots(contributions) == expected


@given(
    contributions=valid_contribution_sets(),
    multiplier=st.integers(min_value=1, max_value=100),
)
def test_generated_positive_scaling_scales_only_chip_values(
    contributions: tuple[PotContribution, ...],
    multiplier: int,
) -> None:
    original = construct_pots(contributions)
    scaled = construct_pots(
        PotContribution(item.player_id, item.committed * multiplier, item.status)
        for item in contributions
    )

    assert len(scaled.pots) == len(original.pots)
    for original_pot, scaled_pot in zip(original.pots, scaled.pots, strict=True):
        assert scaled_pot.tier.lower_threshold == original_pot.tier.lower_threshold * multiplier
        assert scaled_pot.tier.upper_threshold == original_pot.tier.upper_threshold * multiplier
        assert scaled_pot.amount == original_pot.amount * multiplier
        assert scaled_pot.contributors == original_pot.contributors
        assert scaled_pot.eligible_players == original_pot.eligible_players
    assert scaled.total_returned_chips == original.total_returned_chips * multiplier


@given(contributions=valid_contribution_sets())
def test_generated_fold_status_changes_only_eligibility_not_contribution_arithmetic(
    contributions: tuple[PotContribution, ...],
) -> None:
    with_folds = construct_pots(contributions)
    all_live = construct_pots(
        PotContribution(item.player_id, item.committed, LIVE) for item in contributions
    )

    assert arithmetic_signature(with_folds) == arithmetic_signature(all_live)
    assert with_folds.uncalled_excess == all_live.uncalled_excess


@given(
    invalid=st.one_of(
        st.integers(max_value=-1),
        st.booleans(),
        st.floats(),
        st.text(),
        st.none(),
    )
)
def test_generated_invalid_commitments_raise_typed_errors(invalid: object) -> None:
    with pytest.raises(InvalidCommittedChipsError):
        PotContribution(PlayerId("A"), invalid, LIVE)  # type: ignore[arg-type]


@given(first=st.integers(min_value=0), second=st.integers(min_value=0))
def test_generated_duplicate_players_fail_with_typed_error(first: int, second: int) -> None:
    with pytest.raises(DuplicatePotContributorError):
        construct_pots([contribution("A", first), contribution("A", second)])


@given(
    high=st.integers(min_value=1, max_value=10_000),
    low=st.integers(min_value=0, max_value=9_999),
)
def test_generated_unawardable_funded_layers_fail_with_typed_error(
    high: int,
    low: int,
) -> None:
    low %= high
    with pytest.raises(UnawardablePotError):
        construct_pots(
            [
                contribution("A", high, FOLDED),
                contribution("B", high, FOLDED),
                contribution("C", low, LIVE),
            ]
        )
