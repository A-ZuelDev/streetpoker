from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from streetpoker.application import (
    DEFAULT_BIG_BLIND,
    DEFAULT_SMALL_BLIND,
    DEFAULT_STARTING_STACK,
    GuestId,
    InvalidGuestIdError,
    InvalidNicknameError,
    InvalidRoomCodeError,
    InvalidRoomIdError,
    InvalidRoomNameError,
    InvalidRoomSettingsError,
    RoomId,
    RoomSettings,
    normalize_nickname,
    normalize_room_code,
    normalize_room_name,
)


def test_identifiers_require_nonblank_strings() -> None:
    for value_type, error_type in (
        (RoomId, InvalidRoomIdError),
        (GuestId, InvalidGuestIdError),
    ):
        for value in ("", "   ", 7, None):
            with pytest.raises(error_type):
                value_type(value)  # type: ignore[arg-type]


def test_nickname_normalization_is_nfkc_trimmed_and_whitespace_collapsed() -> None:
    assert normalize_nickname("  \uff21lice\t\n Smith  ") == "Alice Smith"


@pytest.mark.parametrize("value", ["", "   ", "name\x00", "a" * 25])
def test_invalid_nicknames_are_rejected(value: str) -> None:
    with pytest.raises(InvalidNicknameError):
        normalize_nickname(value)


def test_room_name_uses_central_display_normalization() -> None:
    assert normalize_room_name("  Friday\t  \uff30oker  ") == "Friday Poker"


@pytest.mark.parametrize("value", ["", "   ", "room\x01", "r" * 61])
def test_invalid_room_names_are_rejected(value: str) -> None:
    with pytest.raises(InvalidRoomNameError):
        normalize_room_name(value)


def test_room_codes_are_trimmed_uppercase_and_strictly_validated() -> None:
    assert normalize_room_code("  abcdefgh  ") == "ABCDEFGH"
    for value in ("ABCDEFG", "ABCDEFGHI", "ABCDEFI2", "ABC-DEF2", 12345678):
        with pytest.raises(InvalidRoomCodeError):
            normalize_room_code(value)  # type: ignore[arg-type]


def test_room_settings_defaults_and_normalization() -> None:
    settings = RoomSettings(room_name="  Home   Game ")

    assert settings.room_name == "Home Game"
    assert settings.small_blind == DEFAULT_SMALL_BLIND
    assert settings.big_blind == DEFAULT_BIG_BLIND
    assert settings.default_starting_stack == DEFAULT_STARTING_STACK
    assert settings.seating_approval_required
    assert settings.max_seats == 6


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"small_blind": 0}, "small blind"),
        ({"small_blind": True}, "small blind"),
        ({"big_blind": 50}, "big blind"),
        ({"big_blind": 25}, "big blind"),
        ({"default_starting_stack": 99}, "starting stack"),
        ({"seating_approval_required": 1}, "approval"),
        ({"max_seats": 6.0}, "six seats"),
        ({"max_seats": Decimal("6")}, "six seats"),
        ({"max_seats": 6 + 0j}, "six seats"),
        ({"max_seats": True}, "six seats"),
        ({"max_seats": False}, "six seats"),
        ({"max_seats": 5}, "six seats"),
        ({"max_seats": 7}, "six seats"),
    ],
)
def test_complete_room_settings_validation_is_atomic(
    changes: dict[str, object],
    message: str,
) -> None:
    values: dict[str, object] = {
        "room_name": "Valid",
        "small_blind": 50,
        "big_blind": 100,
        "default_starting_stack": 10_000,
        "seating_approval_required": True,
        "max_seats": 6,
    }
    values.update(changes)

    with pytest.raises(InvalidRoomSettingsError, match=message):
        RoomSettings(**values)  # type: ignore[arg-type]


def test_room_values_and_settings_are_immutable() -> None:
    room_id = RoomId("room-1")
    settings = RoomSettings(room_name="Home")

    with pytest.raises(FrozenInstanceError):
        room_id.value = "other"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        settings.room_name = "Other"  # type: ignore[misc]


def test_plain_integer_six_is_the_only_valid_max_seats_value() -> None:
    assert RoomSettings(room_name="Home", max_seats=6).max_seats == 6
