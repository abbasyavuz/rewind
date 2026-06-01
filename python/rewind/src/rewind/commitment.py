"""Forensic commitment + auditable redaction.

The signed artifact commits to the PRE-redaction raw bytes (technical plan §3.5):
we hash+sign the raw bytes, then redact in the background. Low-entropy redacted
fields use a keyed HMAC (BLAKE3 keyed mode), NOT a plain salted hash, to defeat
dictionary/preimage attacks during selective disclosure.

v0 ships a regex redactor; the optional `pii` extra swaps in Presidio.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import blake3

# Conservative v0 patterns. Presidio (pii extra) replaces these in production.
_PATTERNS: dict[str, re.Pattern[str]] = {
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "bearer": re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+"),
    "api_key": re.compile(r"(?i)(sk-|key-)[A-Za-z0-9]{16,}"),
    "card": re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
}


@dataclass
class Commitment:
    """Redacted bytes + the auditable transform that produced them. The raw bytes
    themselves are content-addressed and chained by rewind-core (the signed
    commitment target), so we don't recompute CIDs here."""

    redacted: bytes
    transform_desc: bytes
    # field name -> keyed HMAC of the original value (for selective disclosure)
    field_hmacs: dict[str, str]


def _hmac(value: bytes, disclosure_key: bytes) -> str:
    return blake3.blake3(value, key=disclosure_key).hexdigest()


def commit(raw: bytes, disclosure_key: bytes) -> Commitment:
    """Produce redacted bytes + an auditable transform descriptor.

    `disclosure_key` is a 32-byte per-run key held outside the artifact; revealing
    it lets an auditor verify a disclosed field without it being brute-forceable.
    """
    text = raw.decode("utf-8", errors="replace")

    # Collect ALL matches from ALL patterns against the ORIGINAL text first, so every
    # (start, len) is an offset into the unmodified input. Substituting per-pattern in
    # sequence (the old `pat.sub` loop) reported later patterns' offsets relative to an
    # already-shortened string — the recorded spans no longer mapped to the original
    # bytes, breaking the audit trail and selective disclosure. (LB-4)
    matches: list[tuple[int, int, str, str]] = []  # (start, end, kind, original)
    for kind, pat in _PATTERNS.items():
        for m in pat.finditer(text):
            matches.append((m.start(), m.end(), kind, m.group(0)))

    # Resolve overlaps deterministically: sort by start, then by widest match; keep the
    # first and skip any later match that overlaps an already-kept span (e.g. digits a
    # `card` match shares with a `bearer`/`api_key` match).
    matches.sort(key=lambda t: (t[0], -(t[1] - t[0])))
    kept: list[tuple[int, int, str, str]] = []
    last_end = -1
    for start, end, kind, original in matches:
        if start >= last_end:
            kept.append((start, end, kind, original))
            last_end = end

    transform: list[dict[str, object]] = []
    field_hmacs: dict[str, str] = {}
    for start, end, kind, original in kept:
        field_hmacs[f"{kind}@{start}"] = _hmac(original.encode(), disclosure_key)
        transform.append({"kind": kind, "start": start, "len": end - start})

    # Substitute back-to-front so earlier offsets remain valid as we mutate the string.
    for start, end, kind, _original in sorted(kept, key=lambda t: t[0], reverse=True):
        text = text[:start] + f"⟦{kind}⟧" + text[end:]

    redacted = text.encode("utf-8")
    transform_desc = json.dumps(
        {"redactor": "regex-v0", "spans": transform}, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")

    return Commitment(redacted=redacted, transform_desc=transform_desc, field_hmacs=field_hmacs)
