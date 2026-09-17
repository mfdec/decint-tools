"""Pure-Python RSA with PKCS#1 v1.5 / SHA-256 signatures.

The vendor tool generates keys and signs licenses; the runtime stub inside a
built EXE only ever calls :func:`verify`, which is one modular exponentiation.
That is the whole point of doing this by hand: the customer's binary carries
no crypto dependency, no private key, and nothing worth extracting — only the
public modulus.

Keys are 2048-bit by default. Generation takes a few seconds in pure Python,
which is fine for something that happens once per product.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import secrets

# DER prefix of DigestInfo for SHA-256 (RFC 8017 §9.2 note 1).
_DIGESTINFO_SHA256 = bytes.fromhex("3031300d060960864801650304020105000420")

# Odd primes below 2000 — a cheap trial-division sieve before Miller-Rabin.
_SMALL_PRIMES = [
    p for p in range(3, 2000, 2)
    if all(p % q for q in range(3, int(p ** 0.5) + 1, 2))
]


def _is_probable_prime(n: int, rounds: int = 40) -> bool:
    """Miller-Rabin. 40 rounds puts the false-positive chance below 2**-80."""
    if n < 2:
        return False
    for p in _SMALL_PRIMES:
        if n == p:
            return True
        if n % p == 0:
            return False
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(rounds):
        a = secrets.randbelow(n - 3) + 2
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _random_prime(bits: int) -> int:
    while True:
        # Top two bits set so p*q lands on exactly `bits` bits; low bit set = odd.
        c = secrets.randbits(bits) | (1 << (bits - 1)) | (1 << (bits - 2)) | 1
        if _is_probable_prime(c):
            return c


def generate_keypair(bits: int = 2048, e: int = 65537) -> dict:
    """Return ``{"n", "e", "d", "bits"}`` as plain ints (JSON-friendly)."""
    if bits < 1024:
        raise ValueError("RSA keys below 1024 bits are not accepted")
    while True:
        p = _random_prime(bits // 2)
        q = _random_prime(bits // 2)
        if p == q:
            continue
        phi = (p - 1) * (q - 1)
        if math.gcd(e, phi) != 1:
            continue
        n = p * q
        if n.bit_length() != bits:
            continue
        return {"n": n, "e": e, "d": pow(e, -1, phi), "bits": bits}


def _emsa_pkcs1_v15(msg: bytes, k: int) -> bytes:
    t = _DIGESTINFO_SHA256 + hashlib.sha256(msg).digest()
    ps_len = k - len(t) - 3
    if ps_len < 8:
        raise ValueError("RSA modulus too short for SHA-256 PKCS#1 v1.5")
    return b"\x00\x01" + b"\xff" * ps_len + b"\x00" + t


def sign(msg: bytes, n: int, d: int) -> bytes:
    k = (n.bit_length() + 7) // 8
    m = int.from_bytes(_emsa_pkcs1_v15(msg, k), "big")
    return pow(m, d, n).to_bytes(k, "big")


def verify(msg: bytes, sig: bytes, n: int, e: int) -> bool:
    """Constant-time compare of the recovered encoding; never raises."""
    try:
        k = (n.bit_length() + 7) // 8
        if len(sig) != k:
            return False
        s = int.from_bytes(sig, "big")
        if s >= n:
            return False
        em = pow(s, e, n).to_bytes(k, "big")
        return hmac.compare_digest(em, _emsa_pkcs1_v15(msg, k))
    except Exception:
        return False


def fingerprint(n: int, e: int) -> str:
    """Short, human-comparable public-key fingerprint (``ab12-cd34-...``)."""
    h = hashlib.sha256(f"{n}:{e}".encode()).hexdigest()[:20]
    return "-".join(h[i:i + 4] for i in range(0, 20, 4))
