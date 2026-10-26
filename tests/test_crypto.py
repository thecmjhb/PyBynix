"""ChaCha20-Poly1305 against the RFC 8439 vector, and the envelope format.

The SDK only ever opens envelopes, so the tests seal with the primitives directly and
check that the public `decrypt` opens them — through the native backend when
`cryptography` is installed and through the pure-Python one when it is not.
"""

import base64

import pytest

from pybynix import crypto
from pybynix.crypto import (
    NONCE_SIZE,
    ChaCha20Poly1305Error,
    decrypt,
    decrypt_envelope,
    decrypt_json,
)

# RFC 8439 section 2.8.2
KEY = bytes.fromhex("808182838485868788898a8b8c8d8e8f909192939495969798999a9b9c9d9e9f")
NONCE = bytes.fromhex("070000004041424344454647")
AAD = bytes.fromhex("50515253c0c1c2c3c4c5c6c7")
PLAINTEXT = (
    b"Ladies and Gentlemen of the class of '99: If I could offer you only one tip for "
    b"the future, sunscreen would be it."
)
CIPHERTEXT = bytes.fromhex(
    "d31a8d34648e60db7b86afbc53ef7ec2a4aded51296e08fea9e2b5a736ee62d6"
    "3dbea45e8ca9671282fafb69da92728b1a71de0a9e060b2905d6a5b67ecd3b36"
    "92ddbd7f2d778b8c9803aee328091b58fab324e4fad675945585808b4831d7bc"
    "3ff4def08e4b7a9de576d26586cec64b6116"
)
TAG = bytes.fromhex("1ae10b594f09e26a7e902ecbd0600691")


def seal(key, plaintext, nonce=b"\x00" * NONCE_SIZE, aad=b""):
    """Encrypt with the module's own primitives, so the round trip is honest."""
    ciphertext = crypto._chacha20_xor(key, 1, nonce, plaintext)
    one_time_key = crypto._chacha20_block(key, 0, nonce)[:32]
    tag = crypto._poly1305(crypto._seal_mac_data(aad, ciphertext), one_time_key)
    return nonce + ciphertext + tag


def test_rfc8439_vector_with_the_native_backend():
    assert crypto._NATIVE, "cryptography should be available in the test environment"
    assert decrypt(KEY, NONCE, CIPHERTEXT + TAG, AAD) == PLAINTEXT


def test_rfc8439_vector_with_the_pure_python_backend(monkeypatch):
    monkeypatch.setattr(crypto, "_NATIVE", False)
    assert decrypt(KEY, NONCE, CIPHERTEXT + TAG, AAD) == PLAINTEXT


def test_pure_python_poly1305_agrees_with_the_native_one(monkeypatch):
    envelope = seal(KEY, PLAINTEXT, NONCE, AAD)
    sealed = envelope[NONCE_SIZE:]  # `decrypt` takes ciphertext||tag, not the nonce
    native = decrypt(KEY, NONCE, sealed, AAD)
    monkeypatch.setattr(crypto, "_NATIVE", False)
    assert decrypt(KEY, NONCE, sealed, AAD) == native == PLAINTEXT


def test_envelope_is_nonce_then_ciphertext(monkeypatch):
    payload = b'{"candles": []}'
    envelope = base64.b64encode(seal(KEY, payload)).decode()

    assert decrypt_envelope(KEY, envelope) == payload
    monkeypatch.setattr(crypto, "_NATIVE", False)
    assert decrypt_envelope(KEY, envelope) == payload


def test_a_tampered_envelope_is_rejected():
    blob = bytearray(seal(KEY, b'{"price": 1.0}'))
    blob[-1] ^= 0x01
    with pytest.raises(ChaCha20Poly1305Error):
        decrypt_envelope(KEY, base64.b64encode(bytes(blob)).decode())


def test_a_wrong_key_is_rejected():
    envelope = base64.b64encode(seal(KEY, b"secret")).decode()
    with pytest.raises(ChaCha20Poly1305Error):
        decrypt_envelope(bytes(32), envelope)


def test_short_and_malformed_envelopes_are_rejected():
    with pytest.raises(ChaCha20Poly1305Error):
        decrypt_envelope(KEY, base64.b64encode(b"short").decode())
    with pytest.raises(ChaCha20Poly1305Error):
        decrypt_envelope(KEY, "not base64 !!!")
    with pytest.raises(ChaCha20Poly1305Error):
        decrypt(b"short-key", NONCE, b"x" * 32)


def test_decrypt_json_round_trip():
    envelope = base64.b64encode(seal(KEY, b'{"symbol": "EUR/USD OTC", "price": 1.5}')).decode()
    assert decrypt_json(KEY, envelope) == {"symbol": "EUR/USD OTC", "price": 1.5}
