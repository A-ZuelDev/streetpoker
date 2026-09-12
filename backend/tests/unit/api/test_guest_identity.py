import base64

import pytest

from streetpoker.api.guest_identity import derive_guest_id


def token(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def test_guest_identity_is_stable_and_does_not_expose_bearer_token() -> None:
    guest_token = token(bytes(range(32)))

    first = derive_guest_id(guest_token)
    second = derive_guest_id(guest_token)

    assert first == second
    assert first.value.startswith("guest_")
    assert guest_token not in first.value


@pytest.mark.parametrize(
    "guest_token",
    [
        token(bytes(32)) + "=",
        token(bytes(31)),
        token(bytes(33)),
        "!" * 43,
        "é" * 43,
        "A" * 42 + "B",
    ],
    ids=["padding", "short", "long", "alphabet", "unicode", "noncanonical"],
)
def test_guest_identity_rejects_invalid_or_noncanonical_tokens(guest_token: str) -> None:
    with pytest.raises(ValueError):
        derive_guest_id(guest_token)
