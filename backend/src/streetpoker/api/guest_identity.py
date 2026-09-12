"""Browser guest-token validation and transport identity derivation."""

import base64
import hashlib

from streetpoker.application import GuestId


def derive_guest_id(guest_token: str) -> GuestId:
    """Derive a public authorization identity from a private 256-bit bearer token."""
    padding = "=" * (-len(guest_token) % 4)
    try:
        raw = base64.urlsafe_b64decode(guest_token + padding)
    except ValueError as error:
        raise ValueError("Guest token is not valid base64url.") from error
    canonical = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    if len(raw) != 32 or canonical != guest_token:
        raise ValueError("Guest token must canonically encode 32 bytes.")
    return GuestId(f"guest_{hashlib.sha256(raw).hexdigest()}")
