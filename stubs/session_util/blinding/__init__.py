from __future__ import annotations
from typing import overload
import typing

class Keypair:
    """A blinded Ed25519 key pair (pubkey + privkey)."""
    pubkey: bytes = b''
    """The 32-byte blinded Ed25519 public key (prefix 0x15 or 0x25 depending on blinding type)."""
    privkey: bytes = b''
    """The 64-byte blinded Ed25519 secret key (sodium-style: seed + pubkey)."""

@overload
def blind25_id(session_id: str, server_pk: str, /) -> str: ...
@overload
def blind25_id(session_id: bytes, server_pk: bytes, /) -> bytes: ...
def blind25_id(session_id: str | bytes, server_pk: str | bytes, /) -> str | bytes:
    """Compute a blinded session ID using 25xxx-style Community pubkey blinding.

    Takes the (unblinded) Session ID and server pubkey; returns the blinded ID.

    Overloads:
    - str → str:  hex strings (Session ID may include or omit '05' prefix)
                  Returns hex string of the blinded Ed25519 pubkey (prefixed with '25').
    - bytes → bytes: binary form (Session ID 32 or 33 bytes, server_pk 32 bytes)
                     Returns 33-byte blinded pubkey (0x25 prefix + 32 bytes).

    The result is a standard Ed25519 public key usable for verification.
    """
    ...


def blind15_key_pair(ed25519_seckey: bytes, server_pubkey: bytes, /) -> Keypair:
    """Compute a blinded Ed25519 key pair using 15xxx-style Community pubkey blinding.

    Args:
        ed25519_seckey: 64-byte sodium-style Ed25519 secret key (seed + pubkey)
                        or 32-byte seed only (pubkey will be derived).
        server_pubkey:  32-byte community server pubkey (binary).

    Returns:
        Keypair with blinded pubkey (0x15 prefix) and blinded privkey.
    """
    ...

def blind25_key_pair(ed25519_seckey: bytes, server_pubkey: bytes, /) -> Keypair:
    """Compute a blinded Ed25519 key pair using 25xxx-style Community pubkey blinding.

    Args:
        ed25519_seckey: 64-byte sodium-style Ed25519 secret key (seed + pubkey)
                        or 32-byte seed only (pubkey will be derived).
        server_pubkey:  32-byte community server pubkey (binary).

    Returns:
        Keypair with blinded pubkey (0x25 prefix) and blinded privkey.
    """
    ...

@overload
def blind15_sign(ed25519_seckey: bytes, server_pubkey: str, message: bytes, /) -> bytes: ...
@overload
def blind15_sign(ed25519_seckey: bytes, server_pubkey: bytes, message: bytes, /) -> bytes: ...
def blind15_sign(ed25519_seckey: bytes, server_pubkey: str | bytes, message: bytes, /) -> bytes:
    """Sign a message verifiable with the blinded 15xxx pubkey version of a Session ID.

    The signature is a standard Ed25519 signature — verifiable using the blinded pubkey
    returned by blind15_key_pair(...).pubkey.

    Args:
        ed25519_seckey: 64-byte sodium Ed25519 secret key or 32-byte seed.
        server_pubkey:  Community server pubkey (64 hex chars str or 32 bytes).
        message:        Message to sign (arbitrary bytes).

    Returns:
        64-byte Ed25519 signature (bytes).
    """
    ...

@overload
def blind25_sign(ed25519_seckey: bytes, server_pubkey: str, message: bytes, /) -> bytes: ...
@overload
def blind25_sign(ed25519_seckey: bytes, server_pubkey: bytes, message: bytes, /) -> bytes: ...
def blind25_sign(ed25519_seckey: bytes, server_pubkey: str | bytes, message: bytes, /) -> bytes:
    """Sign a message verifiable with the blinded 25xxx pubkey version of a Session ID.

    The signature is a standard Ed25519 signature — verifiable using the blinded pubkey
    returned by blind25_key_pair(...).pubkey.

    Args:
        ed25519_seckey: 64-byte sodium Ed25519 secret key or 32-byte seed.
        server_pubkey:  Community server pubkey (64 hex chars str or 32 bytes).
        message:        Message to sign (arbitrary bytes).

    Returns:
        64-byte Ed25519 signature (bytes).
    """
    ...
