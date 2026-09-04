"""T1: minhash port — shingles, signatures, similarity, LSH candidate pairs."""
from kamino.minhash import shingles, signature, similarity, lsh_buckets


def test_shingles_basic():
    s = shingles("abcdef", 5)
    assert s == {"abcde", "bcdef"}
    assert shingles("", 5) == set()


def test_shingles_collapse_whitespace():
    assert shingles("a b\n\tc", 5) == shingles("a b c", 5)


def test_identical_texts_similarity_near_one():
    t = "the same exact paragraph of text repeated verbatim across sessions " * 20
    a, b = signature(shingles(t, 5), 64), signature(shingles(t, 5), 64)
    assert similarity(a, b) == 1.0


def test_disjoint_texts_similarity_near_zero():
    a = signature(shingles("x" * 5 + "abcdefghij klmnop qrstuv wxyz" * 30, 5), 64)
    b = signature(shingles("1234567890 !@#$%^ NOPQRS TUVWX YZABC" * 30, 5), 64)
    assert similarity(a, b) < 0.15


def test_half_overlap_estimates_jaccard():
    common = {f"common-shingle-{i}" for i in range(500)}
    a = signature(common | {f"only-a-{i}" for i in range(250)}, 128)
    b = signature(common | {f"only-b-{i}" for i in range(250)}, 128)
    est = similarity(a, b)          # true jaccard = 500/1000 = 0.5
    assert 0.35 < est < 0.65


def test_empty_signature_is_none():
    assert signature(set(), 64) is None


def test_similarity_none_or_mismatched_is_zero():
    assert similarity(None, [1, 2]) == 0.0
    assert similarity([1, 2], [1, 2, 3]) == 0.0


def test_lsh_finds_similar_pair_only():
    t1 = "we keep re-deriving the acme-ui pagination architecture every week " * 30
    t2 = "we keep re-deriving the acme-ui pagination architecture every week " * 30 + " small tail"
    t3 = "completely unrelated recipe notes about sourdough starter maintenance " * 30
    sigs = {"a": signature(shingles(t1, 5), 64),
            "b": signature(shingles(t2, 5), 64),
            "c": signature(shingles(t3, 5), 64)}
    pairs = lsh_buckets(sigs, bands=16)
    assert ("a", "b") in pairs or ("b", "a") in pairs
    assert not any({"c"} & set(p) for p in pairs)


def test_lsh_skips_none_signatures():
    sigs = {"a": signature(shingles("some text here repeated " * 30, 5), 64), "b": None}
    assert lsh_buckets(sigs, bands=16) == []
