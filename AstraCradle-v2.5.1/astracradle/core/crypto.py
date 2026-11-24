"""Pure-Python RFC 8032 Ed25519 signature verification & signing (zero external dependencies)."""

from __future__ import annotations

import hashlib

_P = 2**255 - 19
_Q = 2**252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_I = pow(2, (_P - 1) // 4, _P)
_BY = (4 * pow(5, _P - 2, _P)) % _P


def _xrecover(y: int) -> int:
    xx = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P)
    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P != 0:
        x = (x * _I) % _P
    if x % 2 != 0:
        x = _P - x
    return x


_BX = _xrecover(_BY)
_B = (_BX % _P, _BY % _P, 1, (_BX * _BY) % _P)


def _edwards_add(pt1: tuple[int, int, int, int], pt2: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    x1, y1, z1, t1 = pt1
    x2, y2, z2, t2 = pt2
    a = ((y1 - x1) * (y2 - x2)) % _P
    b = ((y1 + x1) * (y2 + x2)) % _P
    c = (2 * _D * t1 * t2) % _P
    d = (2 * z1 * z2) % _P
    e = (b - a) % _P
    f = (d - c) % _P
    g = (d + c) % _P
    h = (b + a) % _P
    return ((e * f) % _P, (g * h) % _P, (f * g) % _P, (e * h) % _P)


def _edwards_double(pt: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    return _edwards_add(pt, pt)


def _scalarmult(pt: tuple[int, int, int, int], e: int) -> tuple[int, int, int, int]:
    res = (0, 1, 1, 0)
    cur = pt
    while e > 0:
        if e & 1:
            res = _edwards_add(res, cur)
        cur = _edwards_double(cur)
        e >>= 1
    return res


def _encodepoint(pt: tuple[int, int, int, int]) -> bytes:
    x, y, z, _ = pt
    inv_z = pow(z, _P - 2, _P)
    xp = (x * inv_z) % _P
    yp = (y * inv_z) % _P
    out = bytearray(yp.to_bytes(32, "little"))
    out[31] |= (xp & 1) << 7
    return bytes(out)


def _decodepoint(s: bytes) -> tuple[int, int, int, int] | None:
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little") & ((1 << 255) - 1)
    if y >= _P:
        return None
    x_sign = (s[31] >> 7) & 1
    xx = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P)
    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P != 0:
        x = (x * _I) % _P
    if (x * x - xx) % _P != 0:
        return None
    if (x & 1) != x_sign:
        x = _P - x
    if x == 0 and x_sign == 1:
        return None
    return (x, y, 1, (x * y) % _P)


def _hint(m: bytes) -> int:
    h = hashlib.sha512(m).digest()
    return int.from_bytes(h, "little")


def derive_public_key(seed32: bytes) -> bytes:
    if len(seed32) != 32:
        raise ValueError("Ed25519 seed must be 32 bytes")
    h = hashlib.sha512(seed32).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return _encodepoint(_scalarmult(_B, a))


def sign_ed25519(seed32: bytes, message: bytes) -> bytes:
    if len(seed32) != 32:
        raise ValueError("Ed25519 seed must be 32 bytes")
    h = hashlib.sha512(seed32).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    prefix = h[32:]
    pk = _encodepoint(_scalarmult(_B, a))
    r = _hint(prefix + message) % _Q
    R = _encodepoint(_scalarmult(_B, r))
    k = _hint(R + pk + message) % _Q
    S = (r + k * a) % _Q
    return R + S.to_bytes(32, "little")


def verify_ed25519(public_key: bytes, message: bytes, signature: bytes) -> bool:
    if len(public_key) != 32 or len(signature) != 64:
        return False
    A = _decodepoint(public_key)
    R = _decodepoint(signature[:32])
    if A is None or R is None:
        return False
    S = int.from_bytes(signature[32:], "little")
    if S >= _Q:
        return False
    k = _hint(signature[:32] + public_key + message) % _Q
    left = _scalarmult(_B, S)
    right = _edwards_add(R, _scalarmult(A, k))
    return _encodepoint(left) == _encodepoint(right)
