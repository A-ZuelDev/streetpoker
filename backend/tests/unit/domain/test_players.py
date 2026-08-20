from dataclasses import FrozenInstanceError

import pytest

from streetpoker.domain import (
    ChipStack,
    InvalidPlayerIdError,
    InvalidPlayerStateError,
    ParticipationStatus,
    PlayerId,
    SeatedPlayer,
)


def test_player_id_has_opaque_exact_value_semantics() -> None:
    player_id = PlayerId(" Player-01 ")

    assert player_id.value == " Player-01 "
    assert player_id != PlayerId("player-01")


def test_player_id_is_immutable_and_hashable() -> None:
    player_id = PlayerId("player-1")

    assert {player_id, PlayerId("player-1")} == {player_id}
    with pytest.raises(FrozenInstanceError):
        player_id.value = "player-2"  # type: ignore[misc]


@pytest.mark.parametrize("value", ["", " ", "\t\r\n"])
def test_blank_player_ids_are_rejected(value: str) -> None:
    with pytest.raises(InvalidPlayerIdError):
        PlayerId(value)


@pytest.mark.parametrize("value", [1, True, None, object()])
def test_non_string_player_ids_are_rejected(value: object) -> None:
    with pytest.raises(InvalidPlayerIdError):
        PlayerId(value)  # type: ignore[arg-type]


def test_participation_status_has_stable_values() -> None:
    assert [status.value for status in ParticipationStatus] == ["sitting_in", "sitting_out"]


def test_seated_player_is_immutable_and_hashable() -> None:
    player = SeatedPlayer(
        player_id=PlayerId("player-1"),
        stack=ChipStack(100),
        status=ParticipationStatus.SITTING_IN,
    )

    assert {player, player} == {player}
    with pytest.raises(FrozenInstanceError):
        player.status = ParticipationStatus.SITTING_OUT  # type: ignore[misc]


def test_sitting_out_player_may_have_zero_stack() -> None:
    player = SeatedPlayer(
        player_id=PlayerId("player-1"),
        stack=ChipStack(0),
        status=ParticipationStatus.SITTING_OUT,
    )

    assert player.stack == ChipStack(0)


def test_sitting_in_player_requires_positive_stack() -> None:
    with pytest.raises(InvalidPlayerStateError):
        SeatedPlayer(
            player_id=PlayerId("player-1"),
            stack=ChipStack(0),
            status=ParticipationStatus.SITTING_IN,
        )


@pytest.mark.parametrize(
    ("player_id", "stack", "status"),
    [
        ("player-1", ChipStack(10), ParticipationStatus.SITTING_IN),
        (PlayerId("player-1"), 10, ParticipationStatus.SITTING_IN),
        (PlayerId("player-1"), ChipStack(10), "sitting_in"),
    ],
)
def test_seated_player_rejects_invalid_component_types(
    player_id: object,
    stack: object,
    status: object,
) -> None:
    with pytest.raises(InvalidPlayerStateError):
        SeatedPlayer(  # type: ignore[arg-type]
            player_id=player_id,
            stack=stack,
            status=status,
        )
