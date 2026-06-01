"""Forensic-redaction tests. Run: pytest (from python/rewind).

Regression cover for LB-4: redaction spans MUST be offsets into the ORIGINAL bytes.
The old per-pattern `pat.sub` loop reported later patterns' offsets relative to an
already-shortened string, so the recorded spans no longer mapped to the original —
breaking the audit trail and selective-disclosure HMAC keys.
"""

from __future__ import annotations

import json

from rewind.commitment import commit

_KEY = b"\x00" * 32


def _spans(c) -> list[dict]:
    return json.loads(c.transform_desc)["spans"]


def test_redacted_bytes_differ_from_original() -> None:
    raw = b"my email is user@example.com"
    c = commit(raw, _KEY)
    assert c.redacted != raw
    assert b"user@example.com" not in c.redacted
    assert "⟦email⟧".encode() in c.redacted


def test_multi_pattern_spans_map_to_original_offsets() -> None:
    # The exact case from the review that produced garbage offsets: an email that
    # shortens the string, followed by an api_key whose offset must still be correct.
    raw = b"Contact: user@example.com and key is sk-1234567890abcdef00"
    c = commit(raw, _KEY)
    spans = _spans(c)
    text = raw.decode()
    by_kind = {s["kind"]: s for s in spans}
    assert set(by_kind) == {"email", "api_key"}
    for kind, s in by_kind.items():
        segment = text[s["start"]: s["start"] + s["len"]]
        if kind == "email":
            assert "@" in segment, f"email span -> {segment!r}"
        if kind == "api_key":
            assert segment.lower().startswith("sk-"), f"api_key span -> {segment!r}"
    # api_key really starts at 37 in the ORIGINAL (not 28 as the old bug reported).
    assert by_kind["api_key"]["start"] == text.index("sk-")


def test_field_hmac_keys_match_span_positions() -> None:
    raw = b"Contact: user@example.com and key is sk-1234567890abcdef00"
    c = commit(raw, _KEY)
    span_keys = {f"{s['kind']}@{s['start']}" for s in _spans(c)}
    assert span_keys == set(c.field_hmacs)


def test_hmac_is_keyed_not_a_plain_hash() -> None:
    raw = b"user@example.com"
    a = commit(raw, b"\x00" * 32).field_hmacs
    b = commit(raw, b"\x11" * 32).field_hmacs
    # Same value, different disclosure key -> different HMAC (keyed, not a bare hash).
    assert list(a.values()) != list(b.values())


def test_overlapping_matches_do_not_double_redact() -> None:
    # A card-like run of digits should not also be sliced by another pattern into
    # overlapping, position-invalidating spans.
    raw = b"card 4111 1111 1111 1111 end"
    c = commit(raw, _KEY)
    spans = sorted(_spans(c), key=lambda s: s["start"])
    for prev, nxt in zip(spans, spans[1:]):
        assert prev["start"] + prev["len"] <= nxt["start"], "spans must not overlap"


def test_no_secret_no_change() -> None:
    raw = b"just some harmless text with no secrets"
    c = commit(raw, _KEY)
    assert c.redacted == raw
    assert _spans(c) == []
    assert c.field_hmacs == {}
