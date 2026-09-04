"""MinHash + LSH over character shingles. Stdlib only.

Char n-grams (not words): language-agnostic, immune to Turkish morphology, and
they catch near-verbatim template reuse regardless of markdown structure.
Ported from the Phase 0 spike unchanged in behavior (docs/archive/spike-phase0/).
"""
import hashlib
import struct
from typing import Optional

_MERSENNE = (1 << 61) - 1  # prime for affine universal hashing


def _base_hash(s: str) -> int:
    return struct.unpack("<Q", hashlib.blake2b(s.encode("utf-8"), digest_size=8).digest())[0]


def _perm_params(perms: int) -> list:
    # deterministic pseudo-random (a, b) pairs derived from the permutation index
    params = []
    for i in range(perms):
        d = hashlib.blake2b(f"perm-{i}".encode(), digest_size=16).digest()
        a = (struct.unpack("<Q", d[:8])[0] % (_MERSENNE - 1)) + 1
        b = struct.unpack("<Q", d[8:])[0] % _MERSENNE
        params.append((a, b))
    return params


def shingles(text: str, k: int) -> set:
    t = " ".join(text.lower().split())  # collapse whitespace so formatting differences vanish
    if len(t) < k:
        return {t} if t else set()
    return {t[i:i + k] for i in range(len(t) - k + 1)}


def signature(shingle_set: set, perms: int) -> Optional[list]:
    if not shingle_set:
        return None
    base = [_base_hash(s) for s in shingle_set]
    sig = []
    for a, b in _perm_params(perms):
        sig.append(min(((a * h + b) % _MERSENNE) for h in base))
    return sig


def similarity(sig_a: Optional[list], sig_b: Optional[list]) -> float:
    if not sig_a or not sig_b or len(sig_a) != len(sig_b):
        return 0.0
    eq = sum(1 for x, y in zip(sig_a, sig_b) if x == y)
    return eq / len(sig_a)


def lsh_buckets(sigs: dict, bands: int) -> list:
    """sigs: {id: signature}. Returns unique candidate pairs sharing >= 1 band bucket."""
    ids = [i for i, s in sigs.items() if s]
    if not ids:
        return []
    rows = len(sigs[ids[0]]) // bands
    buckets: dict = {}
    for cid in ids:
        sig = sigs[cid]
        for b in range(bands):
            key = (b, tuple(sig[b * rows:(b + 1) * rows]))
            buckets.setdefault(key, []).append(cid)
    pairs = set()
    for members in buckets.values():
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                pairs.add(tuple(sorted((members[i], members[j]))))
    return sorted(pairs)
