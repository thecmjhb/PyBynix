"""ChaCha20-Poly1305 (RFC 8439) for the encrypted market envelope.

The web client seals market payloads with an AEAD key handed out by
`POST /api/market/session`. Every sealed value is

    base64( nonce[12] || ciphertext || tag[16] )

and opens with that session key. Only decryption is required here: the SDK never
seals anything.

`cryptography` is used when it is installed because it is much faster on the tick
path. Otherwise a self-contained pure-Python implementation runs, so the SDK keeps
working with no compiled dependency. Both are checked against the RFC 8439 vector
in `tests/test_crypto.py`.
"""

import base64
import binascii
import json

__all__ = [
    "ChaCha20Poly1305Error",
    "decrypt",
    "decrypt_envelope",
    "decrypt_json",
    "NONCE_SIZE",
]

NONCE_SIZE = 12
TAG_SIZE = 16

try:  # optional acceleration
    from cryptography.hazmat.primitives.ciphers.aead import (  # noqa: F401
        ChaCha20Poly1305 as _Native,
    )

    _NATIVE = True
except ImportError:  # pragma: no cover - exercised on minimal installs
    _NATIVE = False


class ChaCha20Poly1305Error(ValueError):
    """The envelope could not be opened (wrong key, wrong format, or tampered)."""


# ── pure Python fallback ─────────────────────────────────────────────────────
_MASK = 0xFFFFFFFF
_SIGMA = (0x61707865, 0x3320646E, 0x79622D32, 0x6B206574)


def _rotl(value, shift):
    return ((value << shift) & _MASK) | (value >> (32 - shift))


def _quarter_round(state, a, b, c, d):
    state[a] = (state[a] + state[b]) & _MASK
    state[d] = _rotl(state[d] ^ state[a], 16)
    state[c] = (state[c] + state[d]) & _MASK
    state[b] = _rotl(state[b] ^ state[c], 12)
    state[a] = (state[a] + state[b]) & _MASK
    state[d] = _rotl(state[d] ^ state[a], 8)
    state[c] = (state[c] + state[d]) & _MASK
    state[b] = _rotl(state[b] ^ state[c], 7)


def _chacha20_block(key, counter, nonce):
    """One 64-byte keystream block."""
    initial = list(_SIGMA)
    initial += [int.from_bytes(key[i : i + 4], "little") for i in range(0, 32, 4)]
    initial.append(counter & _MASK)
    initial += [int.from_bytes(nonce[i : i + 4], "little") for i in range(0, 12, 4)]
    working = list(initial)
    for _ in range(10):  # 20 rounds = 10 column + 10 diagonal
        _quarter_round(working, 0, 4, 8, 12)
        _quarter_round(working, 1, 5, 9, 13)
        _quarter_round(working, 2, 6, 10, 14)
        _quarter_round(working, 3, 7, 11, 15)
        _quarter_round(working, 0, 5, 10, 15)
        _quarter_round(working, 1, 6, 11, 12)
        _quarter_round(working, 2, 7, 8, 13)
        _quarter_round(working, 3, 4, 9, 14)
    return b"".join(((working[i] + initial[i]) & _MASK).to_bytes(4, "little") for i in range(16))


def _chacha20_xor(key, counter, nonce, data):
    out = bytearray(len(data))
    for offset in range(0, len(data), 64):
        block = _chacha20_block(key, counter + offset // 64, nonce)
        chunk = data[offset : offset + 64]
        out[offset : offset + len(chunk)] = bytes(
            a ^ b for a, b in zip(chunk, block, strict=False)
        )
    return bytes(out)


def _poly1305(message, one_time_key):
    r = int.from_bytes(one_time_key[:16], "little") & 0x0FFFFFFC0FFFFFFC0FFFFFFC0FFFFFFF
    s = int.from_bytes(one_time_key[16:], "little")
    modulus = (1 << 130) - 5
    acc = 0
    for offset in range(0, len(message), 16):
        block = message[offset : offset + 16]
        acc = ((acc + int.from_bytes(block + b"\x01", "little")) * r) % modulus
    return ((acc + s) & ((1 << 128) - 1)).to_bytes(16, "little")


def _pad16(data):
    return b"\x00" * (-len(data) % 16)


def _seal_mac_data(aad, ciphertext):
    return (
        aad
        + _pad16(aad)
        + ciphertext
        + _pad16(ciphertext)
        + len(aad).to_bytes(8, "little")
        + len(ciphertext).to_bytes(8, "little")
    )


def _pure_decrypt(key, nonce, sealed, aad):
    if len(sealed) < TAG_SIZE:
        raise ChaCha20Poly1305Error("ciphertext is shorter than the tag")
    ciphertext, tag = sealed[:-TAG_SIZE], sealed[-TAG_SIZE:]
    one_time_key = _chacha20_block(key, 0, nonce)[:32]
    expected = _poly1305(_seal_mac_data(aad, ciphertext), one_time_key)
    if not _constant_time_equals(expected, tag):
        raise ChaCha20Poly1305Error("authentication tag mismatch")
    return _chacha20_xor(key, 1, nonce, ciphertext)


def _constant_time_equals(left, right):
    if len(left) != len(right):
        return False
    result = 0
    for a, b in zip(left, right, strict=True):
        result |= a ^ b
    return result == 0


# ── public ───────────────────────────────────────────────────────────────────
def decrypt(key, nonce, sealed, aad=b""):
    """Open `sealed` (ciphertext || tag) with a 32-byte `key` and 12-byte `nonce`."""
    if len(key) != 32:
        raise ChaCha20Poly1305Error("key must be 32 bytes")
    if len(nonce) != NONCE_SIZE:
        raise ChaCha20Poly1305Error("nonce must be 12 bytes")
    if _NATIVE:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

            return ChaCha20Poly1305(key).decrypt(nonce, sealed, aad or None)
        except Exception as exc:  # noqa: BLE001 - normalise to one error type
            raise ChaCha20Poly1305Error(str(exc)) from None
    return _pure_decrypt(key, nonce, sealed, aad)


def _b64decode(value):
    if not isinstance(value, str):
        raise ChaCha20Poly1305Error("envelope must be a base64 string")
    try:
        return base64.b64decode(value, validate=False)
    except (binascii.Error, ValueError):
        raise ChaCha20Poly1305Error("envelope is not valid base64") from None


def decrypt_envelope(key, envelope):
    """Open `base64(nonce || ciphertext || tag)` and return the plaintext bytes."""
    blob = _b64decode(envelope)
    if len(blob) < NONCE_SIZE + TAG_SIZE:
        raise ChaCha20Poly1305Error("envelope is too short")
    return decrypt(key, blob[:NONCE_SIZE], blob[NONCE_SIZE:])


def decrypt_json(key, envelope):
    """Open an envelope and parse the JSON body inside it."""
    raw = decrypt_envelope(key, envelope)
    try:
        return json.loads(raw)
    except ValueError:
        raise ChaCha20Poly1305Error("envelope did not contain JSON") from None
